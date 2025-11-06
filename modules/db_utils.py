import sqlite3
from datetime import date
from modules.config import DATABASE_FILE, DEFAULT_ECONOMY_CARD_JSON, DEFAULT_COMPANY_OVERVIEW_JSON
import json
import pandas as pd
import streamlit as st

def get_db_connection():
    """Helper function to create a database connection."""
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# --- Daily Inputs ---

def upsert_daily_inputs(selected_date: date, market_news: str, etf_summaries: str) -> bool:
    """Saves or updates the daily inputs for a specific date."""
    try:
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO daily_inputs (date, market_news, etf_summaries)
                VALUES (?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    market_news = excluded.market_news,
                    etf_summaries = excluded.etf_summaries
                """,
                (selected_date.isoformat(), market_news, etf_summaries)
            )
            conn.commit()
        return True
    except sqlite3.Error as e:
        print(f"Error in upsert_daily_inputs: {e}")
        return False

def get_daily_inputs(selected_date: date) -> (str, str):
    """Fetches the daily inputs for a specific date."""
    try:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT market_news, etf_summaries FROM daily_inputs WHERE date = ?",
                (selected_date.isoformat(),)
            ).fetchone()
            if row:
                return row['market_news'], row['etf_summaries']
    except sqlite3.Error as e:
        print(f"Error in get_daily_inputs: {e}")
    return None, None

def get_latest_daily_input_date() -> str:
    """Gets the most recent date from the daily_inputs table."""
    try:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT date FROM daily_inputs ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if row:
                return row['date']
    except sqlite3.Error as e:
        print(f"Error in get_latest_daily_input_date: {e}")
    return None

# --- Economy Card Functions ---

def get_economy_card() -> (str, str):
    """
    Gets the "living" economy card (most recent from the main 'economy_cards' table)
    AND the date of that card.
    Returns (card_json, card_date_iso_string)
    """
    try:
        with get_db_connection() as conn:
            # --- REFACTOR: Renamed table ---
            row = conn.execute(
                "SELECT economy_card_json, date FROM economy_cards ORDER BY date DESC LIMIT 1"
            ).fetchone()
            
            if row and row['economy_card_json']:
                return row['economy_card_json'], row['date']
            else:
                # Fallback if table is empty
                return DEFAULT_ECONOMY_CARD_JSON, None
    except sqlite3.Error as e:
        print(f"Error in get_economy_card: {e}")
        return DEFAULT_ECONOMY_CARD_JSON, None

def get_archived_economy_card(selected_date: date) -> str:
    """Gets a specific economy card from the main table by date."""
    try:
        with get_db_connection() as conn:
            # --- REFACTOR: Renamed table ---
            row = conn.execute(
                "SELECT economy_card_json FROM economy_cards WHERE date = ?",
                (selected_date.isoformat(),)
            ).fetchone()
            if row:
                return row['economy_card_json']
    except sqlite3.Error as e:
        print(f"Error in get_archived_economy_card: {e}")
    return None

# --- Company Card Functions ---

def get_all_tickers_from_db() -> list[str]:
    """Gets all unique tickers from the 'stocks' (notes) table."""
    try:
        with get_db_connection() as conn:
            rows = conn.execute("SELECT DISTINCT ticker FROM stocks ORDER BY ticker ASC").fetchall()
            return [row['ticker'] for row in rows]
    except sqlite3.Error as e:
        print(f"Error in get_all_tickers_from_db: {e}")
        return []

def get_company_card_and_notes(ticker: str, selected_date: date = None) -> (str, str, str):
    """
    Gets the historical notes from 'stocks' table AND the most recent company card
    from 'company_cards' table *before or on* the selected_date.

    If selected_date is None, it gets the absolute most recent card.
    
    Returns: (card_json, historical_notes, card_date_iso_string)
    """
    card_json = None
    historical_notes = ""
    card_date = None

    try:
        with get_db_connection() as conn:
            # 1. Get the historical notes (this is still the single source of truth)
            notes_row = conn.execute(
                "SELECT historical_level_notes FROM stocks WHERE ticker = ?",
                (ticker,)
            ).fetchone()
            if notes_row:
                historical_notes = notes_row['historical_level_notes']

            # 2. Get the "living" card (most recent relative to selected_date)
            if selected_date:
                # This is for back-filling: get the most recent card *before* this date
                card_row = conn.execute(
                    """
                    SELECT company_card_json, date FROM company_cards 
                    WHERE ticker = ? AND date < ?
                    ORDER BY date DESC LIMIT 1
                    """,
                    (ticker, selected_date.isoformat())
                ).fetchone()
            else:
                # This is for the editor: get the absolute most recent card
                card_row = conn.execute(
                    """
                    SELECT company_card_json, date FROM company_cards 
                    WHERE ticker = ?
                    ORDER BY date DESC LIMIT 1
                    """,
                    (ticker,)
                ).fetchone()
            
            if card_row and card_row['company_card_json']:
                card_json = card_row['company_card_json']
                card_date = card_row['date']
            else:
                # Fallback if no card is in the archive for this ticker
                card_json = DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", ticker)
                card_date = None # No date, as it's a default
                
    except sqlite3.Error as e:
        print(f"Error in get_company_card_and_notes: {e}")
        card_json = DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", ticker)
        card_date = None
        
    return card_json, historical_notes, card_date

def get_all_archive_dates() -> list[str]:
    """Gets all unique dates from the economy cards table, most recent first."""
    try:
        with get_db_connection() as conn:
            # --- REFACTOR: Renamed table ---
            rows = conn.execute(
                "SELECT DISTINCT date FROM economy_cards ORDER BY date DESC"
            ).fetchall()
            return [row['date'] for row in rows]
    except sqlite3.Error as e:
        print(f"Error in get_all_archive_dates: {e}")
        return []

def get_all_tickers_for_archive_date(selected_date: date) -> list[str]:
    """Gets all tickers that have a card on a specific date."""
    try:
        with get_db_connection() as conn:
            # --- REFACTOR: Renamed table ---
            rows = conn.execute(
                "SELECT DISTINCT ticker FROM company_cards WHERE date = ? ORDER BY ticker ASC",
                (selected_date.isoformat(),)
            ).fetchall()
            return [row['ticker'] for row in rows]
    except sqlite3.Error as e:
        print(f"Error in get_all_tickers_for_archive_date: {e}")
        return []

def get_archived_company_card(selected_date: date, ticker: str) -> (str, str):
    """Gets a specific company card and its raw summary from a specific date."""
    try:
        with get_db_connection() as conn:
            # --- REFACTOR: Renamed table ---
            row = conn.execute(
                "SELECT company_card_json, raw_text_summary FROM company_cards WHERE date = ? AND ticker = ?",
                (selected_date.isoformat(), ticker)
            ).fetchone()
            if row:
                return row['company_card_json'], row['raw_text_summary']
    except sqlite3.Error as e:
        print(f"Error in get_archived_company_card: {e}")
    return None, None

# --- Functions for DB_VIEWER ---

def get_all_table_names() -> list[str]:
    """Returns a list of all table names in the database."""
    try:
        with get_db_connection() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            # --- REFACTOR: Filter out the sqlite sequence table ---
            return [row['name'] for row in rows if row['name'] != 'sqlite_sequence']
    except sqlite3.Error as e:
        print(f"Error in get_all_table_names: {e}")
        return []

@st.cache_data(ttl=60) # Cache the table data for 60 seconds
def get_table_data(table_name: str) -> pd.DataFrame:
    """Fetches all data from a specific table and returns a DataFrame."""
    try:
        with get_db_connection() as conn:
            df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
            
            # Sort by date descending to see recent entries
            if 'date' in df.columns:
                df = df.sort_values(by='date', ascending=False)

            return df
    except Exception as e:
        print(f"Error in get_table_data for {table_name}: {e}")
        # Return an empty DataFrame on error
        return pd.DataFrame()