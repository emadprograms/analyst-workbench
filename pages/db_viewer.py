import streamlit as st
import pandas as pd
from modules.db_utils import get_all_table_names, get_table_data
from modules.config import DATABASE_FILE # <-- 1. IMPORT THE FILE PATH
import os # <-- Import os to get the full path

st.set_page_config(layout="wide", page_title="Database Viewer")

# --- 2. PRINT THE FILE PATH ---
st.title("Database Viewer")
st.subheader(f"Reading from: {os.path.abspath(DATABASE_FILE)}")
st.markdown("---")
# --- END MODIFICATION ---

st.markdown("A simple interface to view the contents of the application's database tables.")

# Get all table names from the database
table_names = get_all_table_names()

if not table_names:
    st.warning("No tables found in the database. Please run the setup script.")
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