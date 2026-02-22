import libsql_client
from modules.core.config import TURSO_DB_URL, TURSO_AUTH_TOKEN

def inspect():
    # Load secrets
    try:
        if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
            print("❌ CRITICAL: Turso DB URL or Auth Token not found in config/Infisical.")
            return

        db_url = TURSO_DB_URL
        auth_token = TURSO_AUTH_TOKEN

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
                rs_count = client.execute(f"SELECT COUNT(*) FROM {table}")
                print(f"Row Count: {rs_count.rows[0][0]}")
            except Exception as e:
                print(f"Error inspecting {table}: {e}")

        client.close()
        
        # --- 3. Inspect PRICE Database ---
        print("\n--- Inspecting External Price Database ---")
        from modules.core.config import TURSO_PRICE_DB_URL, TURSO_PRICE_AUTH_TOKEN
        if not TURSO_PRICE_DB_URL:
            print("⚠️ TURSO_PRICE_DB_URL not found. Skipping price DB check.")
        else:
            try:
                price_url = TURSO_PRICE_DB_URL.replace("libsql://", "https://")
                price_client = libsql_client.create_client_sync(url=price_url, auth_token=TURSO_PRICE_AUTH_TOKEN)
                print(f"✅ Connected to Price Database: {price_url}")
                rs = price_client.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='market_data'")
                if rs.rows:
                    print("✅ Table 'market_data' FOUND in external database.")
                else:
                    print("❌ Table 'market_data' NOT FOUND in external database.")
                price_client.close()
            except Exception as e:
                print(f"❌ Price DB Check Failed: {e}")

        print("Inspection Complete.")

    except Exception as e:
        print(f"❌ Inspection Failed: {e}")

if __name__ == "__main__":
    inspect()
