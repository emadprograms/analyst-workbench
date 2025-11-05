import sqlite3
import os
import pandas as pd
import streamlit as st
from modules.config import DATABASE_FILE
from datetime import date

# Note: 'split_stock_summaries' has been correctly moved to 'data_processing.py'

def get_db_connection(read_only=True):
    """Establishes and returns a connection to the database."""
    try:
        db_path = os.path.abspath(DATABASE_FILE)
        if read_only:
            # Use a URI for read-only connections
            db_uri = f"file:{db_path}?mode=ro"
            return sqlite3.connect(db_uri, uri=True)
        else:
            return sqlite3.connect(db_path)
    except sqlite3.Error as e:
        st.error(f"Database connection error: {e}")
        return None

def get_all_table_names():
    """Returns a list of all table names in the database."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
            tables = [table[0] for table in cursor.fetchall()]
            return tables
    except sqlite3.Error as e:
        st.error(f"Error fetching table names: {e}")
        return []

def get_table_data(table_name):
    """Fetches all data from a specified table and returns it as a DataFrame."""
    conn = get_db_connection()
    if not conn:
        return pd.DataFrame()
    try:
        with conn:
            # Use LIMIT to prevent loading excessively large tables into memory
            return pd.read_sql_query(f"SELECT * FROM {table_name} LIMIT 1000", conn)
    except pd.io.sql.DatabaseError as e:
        st.warning(f"Could not read table '{table_name}'. It might be empty or invalid. Error: {e}")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"An unexpected error occurred while fetching data from '{table_name}': {e}")
        return pd.DataFrame()

def get_daily_inputs(selected_date: date):
    """Fetches the market news and ETF summaries for a given date."""
    conn = get_db_connection()
    if not conn:
        return None, None
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT market_news, combined_etf_summaries FROM daily_inputs WHERE date = ?",
                (selected_date.isoformat(),)
            )
            data = cursor.fetchone()
            if data:
                return data[0], data[1] # market_news, etf_summaries
            else:
                return None, None
    except sqlite3.Error as e:
        st.error(f"Error fetching daily inputs: {e}")
        return None, None

def get_economy_card():
    """Fetches the current global economy card JSON from the 'living' table."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("SELECT economy_card_json FROM market_context WHERE context_id = 1")
            data = cursor.fetchone()
            if data and data[0]:
                return data[0]
            else:
                return None
    except sqlite3.Error as e:
        st.error(f"Error fetching economy card: {e}")
        return None

def get_company_card_and_notes(ticker: str):
    """Fetches the company card and historical notes for a given ticker from the 'living' table."""
    conn = get_db_connection()
    if not conn:
        return None, None
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT company_overview_card_json, historical_level_notes FROM stocks WHERE ticker = ?",
                (ticker,)
            )
            data = cursor.fetchone()
            if data:
                return data[0], data[1] # card_json, notes
            else:
                return None, None
    except sqlite3.Error as e:
        st.error(f"Error fetching data for {ticker}: {e}")
        return None, None

def get_all_tickers_from_db():
    """Fetches a list of all unique tickers from the 'stocks' table."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn:
            tickers = pd.read_sql_query("SELECT DISTINCT ticker FROM stocks ORDER BY ticker ASC", conn)['ticker'].tolist()
        return tickers
    except Exception as e:
        st.error(f"Error fetching tickers: {e}")
        return []

def upsert_daily_inputs(selected_date: date, market_news: str, etf_summaries: str):
    """Saves or updates the shared inputs for a given day."""
    conn = get_db_connection(read_only=False)
    if not conn:
        return False
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO daily_inputs (date, market_news, combined_etf_summaries)
                VALUES (?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    market_news = excluded.market_news,
                    combined_etf_summaries = excluded.combined_etf_summaries
            """, (selected_date.isoformat(), market_news, etf_summaries))
        return True
    except sqlite3.Error as e:
        st.error(f"Error saving daily inputs: {e}")
        return False

# --- NEW FUNCTIONS FOR ARCHIVE BROWSER ---

def get_all_archive_dates():
    """Fetches all unique dates from the economy_card_archive, sorted descending."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn:
            # Querying economy archive is a good proxy for all archived dates
            dates = pd.read_sql_query(
                "SELECT DISTINCT date FROM economy_card_archive ORDER BY date DESC", 
                conn
            )['date'].tolist()
        return dates
    except Exception as e:
        st.error(f"Error fetching archive dates: {e}")
        return []

def get_all_tickers_for_archive_date(selected_date: date):
    """Fetches all tickers that have a company card archived for a specific date."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn:
            tickers = pd.read_sql_query(
                "SELECT DISTINCT ticker FROM company_card_archive WHERE date = ? ORDER BY ticker ASC",
                conn,
                params=(selected_date.isoformat(),)
            )['ticker'].tolist()
        return tickers
    except Exception as e:
        st.error(f"Error fetching archived tickers: {e}")
        return []

def get_archived_economy_card(selected_date: date):
    """Fetches the archived economy card JSON for a specific date."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT economy_card_json FROM economy_card_archive WHERE date = ?",
                (selected_date.isoformat(),)
            )
            data = cursor.fetchone()
            if data and data[0]:
                return data[0] # card_json
            else:
                return None
    except sqlite3.Error as e:
        st.error(f"Error fetching archived economy card: {e}")
        return None

def get_archived_company_card(selected_date: date, ticker: str):
    """Fetches the archived company card JSON and raw summary for a specific date and ticker."""
    conn = get_db_connection()
    if not conn:
        return None, None
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT company_card_json, raw_text_summary FROM company_card_archive WHERE date = ? AND ticker = ?",
                (selected_date.isoformat(), ticker)
            )
            data = cursor.fetchone()
            if data:
                return data[0], data[1] # card_json, raw_summary
            else:
                return None, None
    except sqlite3.Error as e:
        st.error(f"Error fetching archived company card for {ticker}: {e}")
        return None, None