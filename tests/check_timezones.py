import sys
import os

# Ensure modules can be imported
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.config import TURSO_DB_URL, TURSO_AUTH_TOKEN
from libsql_client import create_client_sync

def check_timestamps():
    config = {
        "url": TURSO_DB_URL.replace("libsql://", "https://"),
        "auth_token": TURSO_AUTH_TOKEN
    }
    client = create_client_sync(**config)

    try:
        # Fetch last 10 rows for SPY
        rs = client.execute("SELECT * FROM market_data WHERE symbol = 'SPY' ORDER BY timestamp DESC LIMIT 10")
        
        print(f"--- Latest 10 Rows for SPY ---")
        for row in rs.rows:
            # Row structure: id, symbol, timestamp, open, high, low, close, volume... 
            # (Assuming standard schema, but printing raw row is safer)
            print(row)
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        client.close()

if __name__ == "__main__":
    check_timestamps()
