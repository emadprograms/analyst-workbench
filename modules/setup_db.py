"""
This script sets up the SQLite database for the EOD data analysis application.

It defines the database schema and creates the necessary tables:
- `stocks`: The "living document" for each stock, holding the most recent analysis.
- `market_context`: The "living document" for the overall market, holding the most recent economy card.
- `daily_inputs`: Stores shared data for a specific day (news, ETF summaries) to avoid redundancy.
- `company_card_archive`: Archives the historical company analysis cards for each day.
- `economy_card_archive`: Archives the historical economy cards for each day.

Running this script directly will create or update the database file.
"""

import sqlite3
import os
import sys

# Add the project root to the Python path to allow for module imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from modules.config import DATABASE_FILE

def setup_database(conn=None):
    """
    Creates or updates the SQLite database and its tables.
    If a connection object is passed, it will be used; otherwise, a new
    connection will be created.
    """
    
    close_conn_on_exit = False
    if conn is None:
        conn = sqlite3.connect(DATABASE_FILE)
        close_conn_on_exit = True

    try:
        cursor = conn.cursor()
        print("Database connection established.")

        # --- 'stocks' table (Living Document for Companies) ---
        # No changes needed here. It holds the most recent card for each ticker.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            ticker TEXT PRIMARY KEY,
            historical_level_notes TEXT,
            company_overview_card_json TEXT,
            last_updated TEXT
        )
        """)
        print("Table 'stocks' is up to date.")

        # --- 'market_context' table (Living Document for Economy) ---
        # No changes needed here. It holds the single most recent economy card.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_context (
            context_id INTEGER PRIMARY KEY CHECK (context_id = 1),
            economy_card_json TEXT,
            last_updated TEXT
        )
        """)
        print("Table 'market_context' is up to date.")

        # --- NEW: 'daily_inputs' table (To prevent data redundancy) ---
        # Stores all data that is shared across all analyses for a single day.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_inputs (
            date TEXT PRIMARY KEY,
            market_news TEXT,
            combined_etf_summaries TEXT
        )
        """)
        print("Table 'daily_inputs' created or updated.")

        # --- NEW: 'company_card_archive' table ---
        # Archives the company-specific analysis for each ticker, each day.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_card_archive (
            archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            raw_text_summary TEXT,
            company_card_json TEXT,
            UNIQUE(date, ticker),
            FOREIGN KEY (date) REFERENCES daily_inputs(date)
        )
        """)
        print("Table 'company_card_archive' created or updated.")

        # --- NEW: 'economy_card_archive' table ---
        # Archives the global economy analysis for each day.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS economy_card_archive (
            archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            economy_card_json TEXT,
            FOREIGN KEY (date) REFERENCES daily_inputs(date)
        )
        """)
        print("Table 'economy_card_archive' created or updated.")

        # --- REMOVED: Old 'data_archive' table ---
        # This table is no longer needed and will be dropped if it exists.
        cursor.execute("DROP TABLE IF EXISTS data_archive")
        print("Old 'data_archive' table removed.")

        # --- Initialize the single row for the market_context table ---
        cursor.execute("""
        INSERT OR IGNORE INTO market_context (context_id, economy_card_json, last_updated)
        VALUES (1, NULL, NULL)
        """)
        print("Initialized 'market_context' row if needed.")

        conn.commit()
        print("Database schema has been successfully updated.")

    except sqlite3.Error as e:
        print(f"An error occurred: {e}")
    finally:
        if close_conn_on_exit and conn:
            conn.close()
            print("Database connection closed.")

if __name__ == "__main__":
    create_database()
