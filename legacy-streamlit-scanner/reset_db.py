import os
import libsql_client
# Remove dotenv dependency, parse manually
# from dotenv import load_dotenv

def get_env_var(name):
    try:
        with open('.env') as f:
            for line in f:
                if line.startswith(f"{name}="):
                    return line.strip().split('=', 1)[1]
    except: return None
    return os.environ.get(name)

url = get_env_var("TURSO_DB_URL")
token = get_env_var("TURSO_AUTH_TOKEN")

if not url or not token:
    print("Error: Missing TURSO credentials in .env")
    exit(1)

url = url.replace("libsql://", "https://")
print(f"Connecting to {url}...")
client = libsql_client.create_client_sync(url=url, auth_token=token)

# 1. BACKUP KEYS
existing_keys = []
try:
    print("Attempting to backup keys...")
    rs = client.execute("SELECT key_name, key_value, priority, added_at FROM gemini_api_keys")
    columns = list(rs.columns)
    for row in rs.rows:
        existing_keys.append(dict(zip(columns, row)))
    print(f"Backed up {len(existing_keys)} keys.")
except Exception as e:
    print(f"Backup failed (maybe table doesn't exist): {e}")

# 2. DROP TABLES
print("Dropping tables...")
try:
    client.execute("DROP TABLE IF EXISTS gemini_key_status")
    client.execute("DROP TABLE IF EXISTS gemini_api_keys") 
    print("Tables dropped.")
except Exception as e:
    print(f"Drop failed: {e}")

# 3. RECREATE TABLES (Using the new schemas)
# We redefine them here to avoid import issues if key_manager has deps
CREATE_KEYS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS gemini_api_keys (
    key_name TEXT PRIMARY KEY NOT NULL,
    key_value TEXT NOT NULL,
    priority INTEGER DEFAULT 10,
    tier TEXT DEFAULT 'free', 
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
CREATE_STATUS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS gemini_key_status (
    key_hash TEXT PRIMARY KEY NOT NULL,
    strikes INTEGER NOT NULL DEFAULT 0,
    release_time REAL NOT NULL DEFAULT 0,
    last_success_day TEXT NOT NULL DEFAULT '',
    last_used_ts REAL NOT NULL DEFAULT 0,
    
    daily_free_lite INTEGER NOT NULL DEFAULT 0,
    daily_free_flash INTEGER NOT NULL DEFAULT 0,
    daily_free_gemma_27b INTEGER NOT NULL DEFAULT 0,
    daily_free_gemma_12b INTEGER NOT NULL DEFAULT 0,

    daily_3_pro INTEGER NOT NULL DEFAULT 0,
    daily_2_5_pro INTEGER NOT NULL DEFAULT 0,
    daily_2_0_flash INTEGER NOT NULL DEFAULT 0,

    ts_3_pro REAL NOT NULL DEFAULT 0,
    ts_2_5_pro REAL NOT NULL DEFAULT 0,
    ts_2_0_flash REAL NOT NULL DEFAULT 0
);
"""

print("Recreating tables...")
client.execute(CREATE_KEYS_TABLE_SQL)
client.execute(CREATE_STATUS_TABLE_SQL)

# 4. RESTORE KEYS
if existing_keys:
    print("Restoring keys...")
    for k in existing_keys:
        try:
            # We insert with tier='free' as default, or whatever logic. 
            # The new schema has 'tier' column.
            client.execute(
                "INSERT INTO gemini_api_keys (key_name, key_value, priority, tier, added_at) VALUES (?, ?, ?, 'free', ?)",
                [k['key_name'], k['key_value'], k['priority'], k['added_at']]
            )
        except Exception as e:
            print(f"Failed to restore key {k.get('key_name')}: {e}")
    print("Keys restored.")
else:
    print("No keys to restore.")

print("Done.")
