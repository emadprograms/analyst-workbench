import streamlit as st
import logging
import libsql_client

# Setup basic logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

def inspect():
    # Load secrets
    try:
        turso_secrets = st.secrets.get("turso", {})
        db_url = turso_secrets.get("db_url")
        auth_token = turso_secrets.get("auth_token")

        if not db_url or not auth_token:
            print("❌ CRITICAL: Turso DB URL or Auth Token not found in st.secrets.")
            return

        # Force HTTPS
        https_url = db_url.replace("libsql://", "https://")
        
        client = libsql_client.create_client_sync(url=https_url, auth_token=auth_token)
        print("✅ Connected to Database.")

        # 1. Inspect KEYS Table
        print("\n--- Inspecting gemini_api_keys ---")
        try:
            rs = client.execute("SELECT * FROM gemini_api_keys LIMIT 0")
            print(f"Columns: {list(rs.columns)}")
            if "tier" in rs.columns:
                print("✅ 'tier' column FOUND.")
            else:
                print("❌ 'tier' column MISSING. (Migration Required for new code)")
        except Exception as e:
            print(f"Error reading keys table: {e}")

        # 2. Inspect STATUS Table
        print("\n--- Inspecting gemini_key_status ---")
        try:
            rs = client.execute("SELECT * FROM gemini_key_status LIMIT 0")
            print(f"Columns: {list(rs.columns)}")
            
            required_cols = ['daily_free_lite', 'daily_3_pro']
            missing = [c for c in required_cols if c not in rs.columns]
            
            if not missing:
                print("✅ New columns (daily_free_lite, etc.) FOUND.")
            else:
                print(f"❌ New columns MISSING: {missing}. (Migration Required)")
        except Exception as e:
            print(f"Error reading status table: {e}")

        # --- INSPECT ALL TABLES ---
        print("\n--- Listing All Tables ---")
        rs = client.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name;")
        tables = [row[0] for row in rs.rows]
        print(f"Tables Found: {tables}")
        
        for table in tables:
            print(f"\n--- Inspecting {table} ---")
            try:
                rs = client.execute(f"SELECT * FROM {table} LIMIT 1")
                print(f"Columns: {list(rs.columns)}")
            except Exception as e:
                print(f"Error inspecting {table}: {e}")

        # --- INSPECT MARKET_DATA SAMPLES ---
        print("\n--- Inspecting Sample Data from market_data ---")
        try:
            rs = client.execute("SELECT * FROM market_data LIMIT 5")
            for row in rs.rows:
                print(list(row))
        except Exception as e:
            print(f"Error inspecting market_data samples: {e}")

        client.close()
        print("Inspection Complete.")

    except Exception as e:
        print(f"❌ Inspection Failed: {e}")

if __name__ == "__main__":
    inspect()
