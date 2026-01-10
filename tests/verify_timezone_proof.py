import sys
import os

# Ensure modules can be imported
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.config import TURSO_DB_URL, TURSO_AUTH_TOKEN
from libsql_client import create_client_sync

def verify_market_open():
    config = {
        "url": TURSO_DB_URL.replace("libsql://", "https://"),
        "auth_token": TURSO_AUTH_TOKEN
    }
    client = create_client_sync(**config)

    # Use yesterday (or a known recent trading day)
    TARGET_DATE = "2026-01-08" 
    
    print(f"--- Checking Volumes for SPY on {TARGET_DATE} ---")

    try:
        # Check 09:30:00 (Possible ET Open OR UTC Pre-Market)
        ts_0930 = f"{TARGET_DATE} 09:30:00"
        rs_0930 = client.execute("SELECT timestamp, volume, close FROM market_data WHERE symbol = 'SPY' AND timestamp = ?", (ts_0930,))
        if rs_0930.rows:
            print(f"Time: 09:30:00 | Volume: {rs_0930.rows[0][1]} | Close: {rs_0930.rows[0][2]}")
        else:
            print(f"Time: 09:30:00 | NO DATA FOUND")

        # Check 14:30:00 (Possible UTC Open OR ET mid-day)
        ts_1430 = f"{TARGET_DATE} 14:30:00"
        rs_1430 = client.execute("SELECT timestamp, volume, close FROM market_data WHERE symbol = 'SPY' AND timestamp = ?", (ts_1430,))
        if rs_1430.rows:
            print(f"Time: 14:30:00 | Volume: {rs_1430.rows[0][1]} | Close: {rs_1430.rows[0][2]}")
        else:
            print(f"Time: 14:30:00 | NO DATA FOUND")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        client.close()

if __name__ == "__main__":
    verify_market_open()
