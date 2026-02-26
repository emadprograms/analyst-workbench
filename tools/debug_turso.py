import os
import logging
import sys

# Add the current directory to sys.path so we can import modules
sys.path.append(os.getcwd())

from modules.core.config import TURSO_DB_URL, TURSO_AUTH_TOKEN
from libsql_client import create_client_sync

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_connection():
    print("-" * 50)
    print("TURSO CONNECTION DIAGNOSTIC")
    print("-" * 50)

    # 1. Check Credentials
    print(f"URL: {TURSO_DB_URL}")
    print(f"Token: {'***' if TURSO_AUTH_TOKEN else 'None'}")

    if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
        print("❌ CRITICAL: Missing Credentials.")
        return

    # 2. Attempt Connection
    try:
        url = TURSO_DB_URL.replace("libsql://", "https://")
        client = create_client_sync(url=url, auth_token=TURSO_AUTH_TOKEN)
        print("✅ Client created successfully.")
    except Exception as e:
        print(f"❌ Client creation failed: {e}")
        return

    # 3. Test Query (get_daily_inputs simulation)
    print("\nTesting 'daily_inputs' query...")
    try:
        rs = client.execute("SELECT * FROM daily_inputs LIMIT 1")
        print(f"✅ Query executed. Rows returned: {len(rs.rows)}")
        
        if rs.rows:
            row = rs.rows[0]
            print(f"Row Type: {type(row)}")
            print(f"Row Data (repr): {repr(row)}")
            
            # Test Access Patterns
            try:
                print(f"Access via index [0]: {row[0]}")
            except Exception as e:
                print(f"❌ Index access failed: {e}")

            try:
                print(f"Access via key ['market_news']: {row['market_news']}")
            except Exception as e:
                print(f"❌ Key access failed: {e}")
                
            try:
                # Try converting to dict if possible
                print(f"Row as dict: {dict(zip(rs.columns, row))}")
            except Exception as e:
                print(f"❌ Dict conversion failed: {e}")
                
    except Exception as e:
        print(f"❌ Query failed: {e}")
    finally:
        client.close()

if __name__ == "__main__":
    test_connection()
