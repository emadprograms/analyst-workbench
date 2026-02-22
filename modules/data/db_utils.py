from __future__ import annotations
from datetime import date
from modules.core.config import (
    DEFAULT_ECONOMY_CARD_JSON, 
    DEFAULT_COMPANY_OVERVIEW_JSON,
    TURSO_DB_URL,
    TURSO_AUTH_TOKEN
)
import json
import pandas as pd
import logging
import libsql_client
from libsql_client import LibsqlError, create_client_sync

def get_db_connection():
    """
    Helper function to create a database connection to Turso.
    This now uses the create_client_sync for Streamlit compatibility
    and forces an HTTPS connection.
    """
    try:
        # --- FIX: Force HTTPS connection ---
        # This is more reliable than libsql:// or wss://
        http_url = TURSO_DB_URL.replace("libsql://", "https://")
        
        config = {
            "url": http_url,
            "auth_token": TURSO_AUTH_TOKEN
        }
        # --- FIX: Use create_client_sync ---
        # This is the synchronous client required
        client = create_client_sync(**config)
        return client
    except Exception as e:
        logging.error(f"Failed to create Turso client: {e}")
        return None

# --- Daily Inputs ---

def upsert_daily_inputs(selected_date: date, market_news: str) -> bool:
    """Saves or updates the daily inputs for a specific date."""
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            print("Error: Database connection failed.")
            return False

        # The Turso client auto-commits; no 'commit()' needed
        conn.execute(
            """
            INSERT INTO daily_inputs (date, market_news)
            VALUES (?, ?)
            ON CONFLICT(date) DO UPDATE SET
                market_news = excluded.market_news
            """,
            (selected_date.isoformat(), market_news)
        )
        return True
    except LibsqlError as e:
        print(f"Error in upsert_daily_inputs: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_daily_inputs(selected_date: date) -> tuple[str | None, str | None]:
    """Fetches the daily inputs for a specific date."""
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            print("Error: Database connection failed.")
            return None, None

        rs = conn.execute(
            "SELECT market_news FROM daily_inputs WHERE date = ?",
            (selected_date.isoformat(),)
        )
        # --- FIX: Use rs.rows ---
        row = rs.rows[0] if rs.rows else None
        if row:
            return row['market_news'], None # Return None for the removed column
    except LibsqlError as e:
        print(f"Error in get_daily_inputs: {e}")
    finally:
        if conn:
            conn.close()
    return None, None # This still satisfies the (val1, val2) unpacking

def get_latest_daily_input_date() -> str:
    """Gets the most recent date from the daily_inputs table."""
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
             print("Error: Database connection failed.")
             return None

        rs = conn.execute(
            "SELECT date FROM daily_inputs ORDER BY date DESC LIMIT 1"
        )
        # --- FIX: Use rs.rows ---
        row = rs.rows[0] if rs.rows else None
        if row:
            return row['date']
    except LibsqlError as e:
        print(f"Error in get_latest_daily_input_date: {e}")
    finally:
        if conn:
            conn.close()
    return None

# --- Economy Card Functions ---

def get_economy_card(before_date: str | None = None) -> tuple[str, str | None]:
    """
    Gets the "living" economy card (most recent).
    If before_date is provided (YYYY-MM-DD), gets the most recent card BEFORE that date.
    """
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            print("Error: Database connection failed.")
            return DEFAULT_ECONOMY_CARD_JSON, None

        if before_date:
            query = "SELECT economy_card_json, date FROM economy_cards WHERE date < ? ORDER BY date DESC LIMIT 1"
            params = (before_date,)
        else:
            query = "SELECT economy_card_json, date FROM economy_cards ORDER BY date DESC LIMIT 1"
            params = ()

        rs = conn.execute(query, params)
        # --- FIX: Use rs.rows ---
        row = rs.rows[0] if rs.rows else None
        
        if row and row['economy_card_json']:
            return row['economy_card_json'], row['date']
        else:
            return DEFAULT_ECONOMY_CARD_JSON, None
    except LibsqlError as e:
        print(f"Error in get_economy_card: {e}")
        return DEFAULT_ECONOMY_CARD_JSON, None
    finally:
        if conn:
            conn.close()

def get_archived_economy_card(selected_date: date) -> tuple[str | None, str | None]:
    """
    Gets a specific economy card AND its raw summary by date.
    """
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            print("Error: Database connection failed.")
            return None, None

        rs = conn.execute(
            "SELECT economy_card_json, raw_text_summary FROM economy_cards WHERE date = ?",
            (selected_date.isoformat(),)
        )
        # --- FIX: Use rs.rows ---
        row = rs.rows[0] if rs.rows else None
        if row:
            return row['economy_card_json'], row['raw_text_summary']
    except LibsqlError as e:
        print(f"Error in get_archived_economy_card: {e}")
    finally:
        if conn:
            conn.close()
    return None, None

# --- Company Card Functions ---

def get_all_tickers_from_db() -> list[str]:
    """Gets all unique tickers from the 'stocks' (notes) table."""
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            print("Error: Database connection failed.")
            return []

        rs = conn.execute("SELECT DISTINCT ticker FROM stocks ORDER BY ticker ASC")
        # --- FIX: Use rs.rows ---
        rows = rs.rows
        return [row['ticker'] for row in rows]
    except LibsqlError as e:
        print(f"Error in get_all_tickers_from_db: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_company_card_and_notes(ticker: str, selected_date: date = None) -> tuple[str, str, str | None]:
    """
    Gets historical notes AND the most recent company card.
    """
    card_json = None
    historical_notes = ""
    card_date = None
    conn = None

    try:
        conn = get_db_connection()
        if not conn:
            print("Error: Database connection failed.")
            return DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", ticker), "", None

        # 1. Get historical notes
        notes_rs = conn.execute(
            "SELECT historical_level_notes FROM stocks WHERE ticker = ?",
            (ticker,)
        )
        # --- FIX: Use .rows ---
        notes_row = notes_rs.rows[0] if notes_rs.rows else None
        if notes_row:
            historical_notes = notes_row['historical_level_notes']

        # 2. Get the "living" card
        card_rs = None # Initialize
        if selected_date:
            card_rs = conn.execute(
                """
                SELECT company_card_json, date FROM company_cards 
                WHERE ticker = ? AND date < ?
                ORDER BY date DESC LIMIT 1
                """,
                (ticker, selected_date.isoformat())
            )
        else:
            card_rs = conn.execute(
                """
                SELECT company_card_json, date FROM company_cards 
                WHERE ticker = ?
                ORDER BY date DESC LIMIT 1
                """,
                (ticker,)
            )
        
        # --- FIX: Use .rows ---
        card_row = card_rs.rows[0] if card_rs.rows else None
        if card_row and card_row['company_card_json']:
            card_json = card_row['company_card_json']
            card_date = card_row['date']
        else:
            card_json = DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", ticker)
            card_date = None
            
    except LibsqlError as e:
        print(f"Error in get_company_card_and_notes: {e}")
        card_json = DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", ticker)
        card_date = None
    finally:
        if conn:
            conn.close()
        
    return card_json, historical_notes, card_date

def get_all_archive_dates() -> list[str]:
    """Gets all unique dates from the economy cards table, most recent first."""
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            print("Error: Database connection failed.")
            return []

        rs = conn.execute(
            "SELECT DISTINCT date FROM economy_cards ORDER BY date DESC"
        )
        # --- FIX: Use rs.rows ---
        rows = rs.rows
        return [row['date'] for row in rows]
    except LibsqlError as e:
        print(f"Error in get_all_archive_dates: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_all_tickers_for_archive_date(selected_date: date) -> list[str]:
    """Gets all tickers that have a card on a specific date."""
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            print("Error: Database connection failed.")
            return []

        rs = conn.execute(
            "SELECT DISTINCT ticker FROM company_cards WHERE date = ? ORDER BY ticker ASC",
            (selected_date.isoformat(),)
        )
        # --- FIX: Use rs.rows ---
        rows = rs.rows
        return [row['ticker'] for row in rows]
    except LibsqlError as e:
        print(f"Error in get_all_tickers_for_archive_date: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_archived_company_card(selected_date: date, ticker: str) -> tuple[str | None, str | None]:
    """Gets a specific company card and its raw summary from a specific date."""
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            print("Error: Database connection failed.")
            return None, None

        rs = conn.execute(
            "SELECT company_card_json, raw_text_summary FROM company_cards WHERE date = ? AND ticker = ?",
            (selected_date.isoformat(), ticker)
        )
        # --- FIX: Use rs.rows ---
        row = rs.rows[0] if rs.rows else None
        if row:
            return row['company_card_json'], row['raw_text_summary']
    except LibsqlError as e:
        print(f"Error in get_archived_company_card: {e}")
    finally:
        if conn:
            conn.close()
    return None, None

# --- Functions for DB_VIEWER ---

def get_all_table_names() -> list[str]:
    """Returns a list of all table names in the database."""
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            print("Error: Database connection failed.")
            return []

        rs = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        # --- FIX: Use rs.rows ---
        rows = rs.rows
        return [row['name'] for row in rows if row['name'] != 'sqlite_sequence']
    except LibsqlError as e:
        print(f"Error in get_all_table_names: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_table_data(table_name: str) -> pd.DataFrame:
    """Fetches all data from a specific table and returns a DataFrame."""
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            print("Error: Database connection failed.")
            return pd.DataFrame()

        rs = conn.execute(f"SELECT * FROM {table_name}")
        # --- FIX: Use rs.rows and rs.columns ---
        rows = rs.rows
        column_names = rs.columns  # <-- THIS IS THE FIX
        
        if not rows:
             return pd.DataFrame([], columns=column_names)

        df = pd.DataFrame(rows, columns=column_names)
        
        if 'date' in df.columns:
            df = df.sort_values(by='date', ascending=False)
        return df
    except Exception as e:
        print(f"Error in get_table_data for {table_name}: {e}")
        return pd.DataFrame()
    finally:
        if conn:
            conn.close()