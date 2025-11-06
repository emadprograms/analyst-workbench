import sqlite3
import os

# Import the database file path from config
try:
    from modules.config import DATABASE_FILE
except ImportError:
    # Fallback if config is not set up (e..g, running standalone)
    # --- THIS IS OUR NEW, CLEAN DATABASE ---
    DATABASE_FILE = "analysis_database.db"

def create_tables():
    """
    Creates all necessary tables in the database with the NEW, SIMPLIFIED schema.
    This script is idempotent and can be run safely.
    """
    
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        print("\n--- Creating new, simplified tables... ---")

        # --- 1. Daily Inputs Table ---
        # This is our "anchor" table for what date has been processed.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_inputs (
            date TEXT PRIMARY KEY,
            market_news TEXT,
            etf_summaries TEXT
        );
        """)
        print("  Created table 'daily_inputs'.")

        # --- 2. Stocks Table (for Historical Notes ONLY) ---
        # This table no longer holds any "living card" JSON.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            ticker TEXT PRIMARY KEY,
            historical_level_notes TEXT
        );
        """)
        print("  Created table 'stocks'.")

        # --- 3. Economy Cards Table (The NEW Source of Truth) ---
        # Renamed from "economy_card_archive".
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS economy_cards (
            date TEXT PRIMARY KEY,
            economy_card_json TEXT
        );
        """)
        print("  Created table 'economy_cards'.")

        # --- 4. Company Cards Table (The NEW Source of Truth) ---
        # Renamed from "company_card_archive".
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_cards (
            date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            raw_text_summary TEXT,
            company_card_json TEXT,
            PRIMARY KEY (date, ticker)
        );
        """)
        print("  Created table 'company_cards'.")

        # --- 5. Drop Obsolete Tables (if they exist) ---
        # We no longer need market_context at all.
        cursor.execute("DROP TABLE IF EXISTS market_context;")
        print("  Dropped obsolete table 'market_context' (if it existed).")
        # --- These are now replaced by the tables above ---
        cursor.execute("DROP TABLE IF EXISTS company_card_archive;")
        print("  Dropped obsolete table 'company_card_archive' (if it existed).")
        cursor.execute("DROP TABLE IF EXISTS economy_card_archive;")
        print("  Dropped obsolete table 'economy_card_archive' (if it existed).")


        conn.commit()
        print("\n--- Database setup complete with new schema! ---")

    except sqlite3.Error as e:
        print(f"An error occurred during database setup: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    # This allows you to run this file directly from the terminal
    # to set up or reset your database.
    
    # Check if the DB already exists to ask for confirmation
    if os.path.exists(DATABASE_FILE):
        confirm = input(
            f"WARNING: Database '{DATABASE_FILE}' already exists.\n"
            "This will DROP old/obsolete tables and create the new, correct schema.\n"
            "Are you sure you want to continue? (y/n): "
        )
        if confirm.lower() != 'y':
            print("Operation cancelled.")
            exit()
            
    create_tables()