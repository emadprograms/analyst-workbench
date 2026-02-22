import libsql_client
from libsql_client import LibsqlError
from modules.core.config import TURSO_DB_URL, TURSO_AUTH_TOKEN

def create_tables():
    """
    Creates all necessary tables in the Turso database.
    This script is idempotent and can be run safely.
    """
    
    if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
        print("Error: TURSO_DB_URL and TURSO_AUTH_TOKEN environment variables must be set.")
        return

    print(f"Connecting to Turso at: {TURSO_DB_URL}...")
    client = None
    try:
        # --- FIX: Force HTTPS connection ---
        http_url = TURSO_DB_URL.replace("libsql://", "https://")
        
        config = {
            "url": http_url,
            "auth_token": TURSO_AUTH_TOKEN
        }
        client = libsql_client.create_client_sync(**config)
        
        print("\n--- Running Schema Setup on Turso... ---")

        # Use a 'batch' operation to send all commands at once
        statements = [
            # Drop old tables first to ensure schema is clean
            # We preserve 'stocks' (now 'aw_ticker_notes')
            "DROP TABLE IF EXISTS daily_inputs;",
            "DROP TABLE IF EXISTS economy_cards;",
            "DROP TABLE IF EXISTS company_cards;",
            "DROP TABLE IF EXISTS data_archive;",
            "DROP TABLE IF EXISTS market_context;",
            "DROP TABLE IF EXISTS company_card_archive;",
            "DROP TABLE IF EXISTS economy_card_archive;",
            
            # --- 1. Daily Inputs Table ---
            """
            CREATE TABLE IF NOT EXISTS aw_daily_news (
                target_date TEXT PRIMARY KEY,
                news_text TEXT
            );
            """,
            
            # --- 2. Stocks Table (for Historical Notes ONLY) ---
            # We DON'T drop this one to preserve notes
            """
            CREATE TABLE IF NOT EXISTS aw_ticker_notes (
                ticker TEXT PRIMARY KEY,
                historical_level_notes TEXT
            );
            """,

            # --- 3. Economy Cards Table ---
            """
            CREATE TABLE IF NOT EXISTS aw_economy_cards (
                date TEXT PRIMARY KEY,
                raw_text_summary TEXT,
                economy_card_json TEXT
            );
            """,

            # --- 4. Company Cards Table ---
            """
            CREATE TABLE IF NOT EXISTS aw_company_cards (
                date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                raw_text_summary TEXT,
                company_card_json TEXT,
                PRIMARY KEY (date, ticker)
            );
            """,

            # --- 5. Data Archive Table (Image Parser / Misc) ---
            """
            CREATE TABLE IF NOT EXISTS aw_data_archive (
                date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                raw_text_summary TEXT,
                PRIMARY KEY (date, ticker)
            );
            """
        ]
        
        # Execute the batch
        client.batch(statements)

        print("  Created/Verified 'aw_ticker_notes' table.")
        print("  Created/Verified 'aw_daily_news' table.")
        print("  Created/Verified 'aw_economy_cards' table.")
        print("  Created/Verified 'aw_company_cards' table.")
        print("  Created/Verified 'aw_data_archive' table.")
        print("  Dropped all obsolete tables.")
        print("\n--- Turso Database setup complete! ---")

    except Exception as e:
        print(f"An error occurred during database setup: {e}")
    finally:
        if client:
            client.close()

if __name__ == "__main__":
    confirm = input(
        "WARNING: This will connect to your LIVE TURSO database.\n"
        "It will PRESERVE the 'stocks' table (with your notes) but will\n"
        "WIPE and RECREATE 'daily_inputs', 'company_cards', 'economy_cards', and 'data_archive'.\n"
        "This will DELETE all existing processed data. Are you sure? (y/n): "
    )
    if confirm.lower() != 'y':
        print("Operation cancelled.")
        exit()
        
    create_tables()