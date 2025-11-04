# db_utils.py

import sqlite3
import os
import pandas as pd
import streamlit as st
from modules.config import DATABASE_FILE

def get_all_tickers_from_db():
    if not os.path.exists(DATABASE_FILE): return []
    conn=None; tickers=[]
    try: 
        conn=sqlite3.connect(DATABASE_FILE)
        tickers=pd.read_sql_query("SELECT DISTINCT ticker FROM stocks ORDER BY ticker ASC",conn)['ticker'].tolist()
    except Exception as e: 
        st.error(f"Err fetch tickers:{e}")
    finally:
        if conn: conn.close()
    return tickers
