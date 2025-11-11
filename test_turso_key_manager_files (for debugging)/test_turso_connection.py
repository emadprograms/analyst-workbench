import streamlit as st
import pandas as pd
from datetime import date
from modules.config import TURSO_DB_URL, TURSO_AUTH_TOKEN
from modules.db_utils import (
    get_db_connection, 
    get_all_table_names, 
    get_table_data, 
    upsert_daily_inputs
)
from libsql_client import LibsqlError

st.set_page_config(page_title="Turso Connection Test", layout="wide")
st.title("Turso Database Connection Test")

# --- 1. Check Secrets ---
st.header("1. Configuration Check")
if TURSO_DB_URL and TURSO_AUTH_TOKEN:
    st.success(f"Secrets loaded successfully.\n\n**URL:** `{TURSO_DB_URL}`")
else:
    st.error("Turso DB URL or Auth Token not found in st.secrets.")
    st.info("Please add `[turso]` section to your `.streamlit/secrets.toml` file.")
    st.code("""
[turso]
db_url = "libsql://your-db-name.turso.io"
auth_token = "your-auth-token"
    """)
    st.stop()

# --- 2. Connection Test ---
st.header("2. Connection Test")
try:
    with st.spinner("Connecting to Turso..."):
        client = get_db_connection()
        # Perform a simple query to test
        rs = client.execute("SELECT 1")
        # --- FIX: Access the .rows property ---
        rows = rs.rows
        result = rows[0] if rows else None # Get the first row
        client.close()
    
    if result and result[0] == 1:
        st.success("✅ Connection Successful! Received '1' from database.")
    else:
        st.warning("Connection seemed to work, but did not get expected '1'.")

except LibsqlError as e:
    st.error(f"❌ Connection FAILED.\n\n**Error:** `{e}`")
    st.info("This usually means your URL or Auth Token is incorrect, or the database server is down.")
    st.stop()
except Exception as e:
    st.error(f"❌ An unexpected error occurred: `{e}`")
    st.stop()

st.divider()

# --- 3. Read Test ---
st.header("3. Read Test: View Tables & Data")

try:
    table_names = get_all_table_names()
    if not table_names:
        st.warning("Could not find any tables. This is OK if you haven't run your `dp.py` schema script yet.")
    else:
        st.success(f"Found {len(table_names)} tables: `{', '.join(table_names)}`")

        selected_table = st.selectbox("Select a table to view its data:", table_names)
        
        if selected_table:
            with st.spinner(f"Loading data from '{selected_table}'..."):
                df = get_table_data(selected_table)
                st.dataframe(df)

except Exception as e:
    st.error(f"Error during read test: {e}")

st.divider()

# --- 4. Write Test ---
st.header("4. Write Test: Save Daily Input")
st.info("This form will write to the `daily_inputs` table. You should see the result in the table viewer above after saving.")

with st.form("write_test_form"):
    test_date = st.date_input("Date", value=date.today())
    test_news = st.text_area("Market News", "This is a test entry from the Streamlit app.")
    
    submitted = st.form_submit_button("Save Test Input")

if submitted:
    with st.spinner("Saving data to Turso..."):
        success = upsert_daily_inputs(
            selected_date=test_date, 
            market_news=test_news, 
            stock_summaries="Test stock summary data"
        )
    
    if success:
        st.success(f"✅ Successfully saved data for {test_date.isoformat()}!")
        st.info("Reloading table data...")
        # Clear the cache for get_table_data and rerun
        st.cache_data.clear()
        st.rerun()
    else:
        st.error("❌ Failed to save data. Check console logs for error.")