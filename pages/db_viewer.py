import streamlit as st
import pandas as pd
import sqlite3
import os

# --- 1. DEFINE LOCAL DB PATH ---
# Assumes 'analysis_database.db' is in the same root folder as this script
DATABASE_FILE = "analysis_database.db"

# --- 2. CREATE LOCAL-ONLY DB FUNCTIONS ---
# These functions are copied from your old setup and use sqlite3

def get_db_connection():
    """Helper function to create a local database connection."""
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def get_all_table_names() -> list[str]:
    """Returns a list of all table names from the local database."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            rows = cursor.fetchall()
            return [row['name'] for row in rows if row['name'] != 'sqlite_sequence']
    except sqlite3.Error as e:
        st.error(f"Error reading local tables: {e}")
        return []

@st.cache_data(ttl=30)
def get_table_data(table_name: str) -> pd.DataFrame:
    """Fetches all data from a specific local table and returns a DataFrame."""
    try:
        with get_db_connection() as conn:
            df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
            if 'date' in df.columns:
                df = df.sort_values(by='date', ascending=False)
            return df
    except Exception as e:
        st.error(f"Error reading local table {table_name}: {e}")
        return pd.DataFrame()

# --- 3. STREAMLIT APP UI ---
st.set_page_config(layout="wide", page_title="Local DB Viewer")
st.title("Local SQLite Database Viewer (Old DB)")

# --- Check if the file exists ---
if not os.path.exists(DATABASE_FILE):
    st.error(f"Error: Database file not found at `{os.path.abspath(DATABASE_FILE)}`")
    st.info("Please make sure your `analysis_database.db` file is in the same directory as this script.")
    st.stop()

st.subheader(f"Reading from: {os.path.abspath(DATABASE_FILE)}")
st.markdown("---")

# Get all table names from the database
table_names = get_all_table_names()

if not table_names:
    st.warning("No tables found in the database. The file might be empty.")
    st.stop()

# Create a tab for each table
tabs = st.tabs(table_names)

for i, table_name in enumerate(table_names):
    with tabs[i]:
        st.subheader(f"Table: `{table_name}`")
        
        # Add a refresh button for each table
        if st.button(f"Refresh {table_name}", key=f"refresh_{table_name}"):
            st.cache_data.clear() # Clear the cache to get fresh data
        
        # Fetch and display the data for the current table
        try:
            df = get_table_data(table_name)
            if not df.empty:
                st.dataframe(df, use_container_width=True)
            else:
                st.info(f"The table `{table_name}` is currently empty.")
        except Exception as e:
            st.error(f"Could not load data for table `{table_name}`. Error: {e}")