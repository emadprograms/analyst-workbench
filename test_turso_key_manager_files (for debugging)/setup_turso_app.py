import streamlit as st
import libsql_client
from libsql_client import LibsqlError
from modules.config import TURSO_DB_URL, TURSO_AUTH_TOKEN

def run_turso_setup():
    """
    Connects to Turso and runs the batch of setup commands.
    Returns (success, message)
    """
    client = None
    try:
        http_url = TURSO_DB_URL.replace("libsql://", "https://")
        
        config = {
            "url": http_url,
            "auth_token": TURSO_AUTH_TOKEN
        }
        client = libsql_client.create_client_sync(**config)
        
        st.write("Connection successful. Running schema setup...")

        # Use a 'batch' operation to send all commands at once
        statements = [
            # Drop old tables first to ensure schema is clean
            # We preserve 'stocks'
            "DROP TABLE IF EXISTS daily_inputs;",
            "DROP TABLE IF EXISTS economy_cards;",
            "DROP TABLE IF EXISTS company_cards;",
            "DROP TABLE IF EXISTS market_context;",
            "DROP TABLE IF EXISTS company_card_archive;",
            "DROP TABLE IF EXISTS economy_card_archive;",
            
            # --- 1. Daily Inputs Table (MODIFIED) ---
            """
            CREATE TABLE IF NOT EXISTS daily_inputs (
                date TEXT PRIMARY KEY,
                market_news TEXT
            );
            """,
            
            # --- 2. Stocks Table (for Historical Notes ONLY) ---
            # We DON'T drop this one to preserve notes
            """
            CREATE TABLE IF NOT EXISTS stocks (
                ticker TEXT PRIMARY KEY,
                historical_level_notes TEXT
            );
            """,

            # --- 3. Economy Cards Table ---
            """
            CREATE TABLE IF NOT EXISTS economy_cards (
                date TEXT PRIMARY KEY,
                raw_text_summary TEXT,
                economy_card_json TEXT
            );
            """,

            # --- 4. Company Cards Table ---
            """
            CREATE TABLE IF NOT EXISTS company_cards (
                date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                raw_text_summary TEXT,
                company_card_json TEXT,
                PRIMARY KEY (date, ticker)
            );
            """
        ]
        
        # Execute the batch
        client.batch(statements)
        
        return True, "Database setup complete! 'stocks' preserved, others reset."

    except Exception as e:
        return False, f"An error occurred: {e}"
    finally:
        if client:
            client.close()

# --- Streamlit App UI ---
st.set_page_config(page_title="Turso DB Setup", layout="centered")
st.title("Turso Database Setup Utility")

st.header("1. Credentials Check")
if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
    st.error("Error: Turso secrets not found in .streamlit/secrets.toml")
    st.info("Please make sure your `.streamlit/secrets.toml` file has the `[turso]` section.")
    st.stop()
else:
    st.success("Turso credentials loaded from st.secrets successfully.")
    st.write(f"**Database URL:** `{TURSO_DB_URL}`")

st.header("2. Run Setup")
st.warning(
    """
    **WARNING:** This will connect to your LIVE Turso database.
    
    - It will **PRESERVE** the `stocks` table (with your notes).
    - It will **WIPE and RECREATE** the `daily_inputs`, `company_cards`, and `economy_cards` tables.
    
    This will DELETE all existing processed data in those tables.
    """
)

if st.button("Initialize/Reset Turso Database"):
    with st.spinner("Connecting to Turso and running setup..."):
        success, message = run_turso_setup()
    
    if success:
        st.success(f"✅ {message}")
        st.balloons()
    else:
        st.error(f"❌ {message}")