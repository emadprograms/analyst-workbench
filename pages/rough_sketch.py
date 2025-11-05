
import streamlit as st
import libsql
import os
import pandas as pd
from datetime import datetime

st.set_page_config(layout="wide")

st.title("Turso Database Sketchpad with `libsql`")

# Get Turso credentials from Streamlit secrets
try:
    db_url = st.secrets["turso"]["db_url"]
    db_token = st.secrets["turso"]["db_token"]
except KeyError:
    st.error("Turso database credentials not found in .streamlit/secrets.toml")
    st.stop()

@st.cache_resource
def get_db_connection():
    """Establishes a connection to the Turso database using libsql."""
    try:
        # Using embedded replica feature of libsql
        conn = libsql.connect("analyst_workbench_replica.db", sync_url=db_url, auth_token=db_token)
        conn.sync()
        return conn
    except Exception as e:
        st.error(f"Failed to connect to Turso DB with libsql: {e}")
        return None

def setup_database(conn):
    """Creates the 'stocks' table based on setup_db.py."""
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            ticker TEXT PRIMARY KEY,
            historical_level_notes TEXT,
            company_overview_card_json TEXT,
            last_updated TEXT
        )
        """)
        st.success("Database table 'stocks' is ready.")
    except Exception as e:
        st.error(f"Error setting up database table: {e}")

def fetch_all_stocks(conn):
    """Fetches all stock records from the database."""
    try:
        cursor = conn.execute("SELECT * FROM stocks ORDER BY ticker ASC")
        rows = cursor.fetchall()
        if rows:
            df = pd.DataFrame(rows, columns=[d[0] for d in cursor.description])
            return df
        else:
            return pd.DataFrame()
    except Exception as e:
        st.error(f"Error fetching stocks: {e}")
        return pd.DataFrame()

def main():
    """Main function to run the Streamlit app."""
    conn = get_db_connection()
    if not conn:
        return

    st.sidebar.button("Setup Database Table", on_click=setup_database, args=(conn,))

    st.header("Insert New Stock Note")
    with st.form("new_stock_form", clear_on_submit=True):
        ticker = st.text_input("Ticker Symbol (e.g., AAPL)")
        historical_notes = st.text_area("Historical Level Notes")
        submitted = st.form_submit_button("Add/Update Note")

        if submitted and ticker:
            try:
                # Using INSERT OR REPLACE to handle both new and existing tickers
                conn.execute(
                    "INSERT OR REPLACE INTO stocks (ticker, historical_level_notes, last_updated) VALUES (?, ?, ?)",
                    (ticker, historical_notes, datetime.now().isoformat())
                )
                conn.commit()
                conn.sync() # Sync changes to the primary database
                st.success(f"Successfully added/updated note for {ticker}.")
                st.cache_resource.clear() # Clear cache to refresh data
            except Exception as e:
                st.error(f"Error adding stock note: {e}")

    st.header("Stocks in Database")
    if st.button("Refresh Stock List"):
        st.cache_resource.clear()
        st.rerun()

    stocks_df = fetch_all_stocks(conn)
    if not stocks_df.empty:
        st.dataframe(stocks_df, use_container_width=True)
    else:
        st.info("No stocks found in the database. Add one using the form above.")

if __name__ == "__main__":
    main()
