
import os
import streamlit as st
from modules.db_utils import get_db_connection
import pandas as pd

def check_paxg_counts():
    conn = get_db_connection()
    if not conn:
        print("Failed to connect to DB")
        return

    print("--- PAXGUSDT Row Counts Grouped by Date ---")
    
    # We query raw timestamps, extract the date part (approximate string slicing for simplicity)
    # SQLite/LibSQL syntax for date extraction: substr(timestamp, 1, 10)
    query = """
    SELECT 
        substr(timestamp, 1, 10) as date_str, 
        COUNT(*) as row_count 
    FROM market_data 
    WHERE symbol = 'PAXGUSDT' 
    GROUP BY date_str 
    ORDER BY date_str DESC
    LIMIT 10;
    """
    
    try:
        rs = conn.execute(query)
        rows = list(rs.rows)
        if not rows:
            print("No data found for PAXGUSDT.")
        else:
            for row in rows:
                print(f"Date: {row[0]} | Rows: {row[1]}")
                
        # Also let's inspect the specific problematic 34 rows for the most recent date
        if rows:
            latest_date = rows[0][0]
            print(f"\n--- Detailed inspection for {latest_date} ---")
            q2 = f"SELECT timestamp, open, close, volume, session FROM market_data WHERE symbol = 'PAXGUSDT' AND timestamp LIKE '{latest_date}%' LIMIT 40"
            rs2 = conn.execute(q2)
            print(f"First 40 rows for {latest_date}:")
            for r in rs2.rows:
                print(r)
                
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_paxg_counts()
