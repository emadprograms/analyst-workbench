import streamlit as st
import logging
import libsql_client

# Setup basic logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

def migrate():
    # Load secrets
    try:
        turso_secrets = st.secrets.get("turso", {})
        db_url = turso_secrets.get("db_url")
        auth_token = turso_secrets.get("auth_token")

        if not db_url or not auth_token:
            log.critical("❌ CRITICAL: Turso DB URL or Auth Token not found in st.secrets.")
            return

        # Force HTTPS
        https_url = db_url.replace("libsql://", "https://")
        
        client = libsql_client.create_client_sync(url=https_url, auth_token=auth_token)
        log.info("✅ Connected to Database.")

        # 1. Update KEYS Table Schema
        log.info("--- 1. Migrating KEYS Table ---")
        try:
            # Try to add the column. If it exists, this might fail or ignore depending on SQLite version,
            # but usually SQLite throws an error if column exists. 
            # We wrap in try/except to be safe.
            client.execute("ALTER TABLE gemini_api_keys ADD COLUMN tier TEXT DEFAULT 'free'")
            log.info("✅ Added 'tier' column to gemini_api_keys.")
        except Exception as e:
            if "duplicate column name" in str(e).lower():
                log.info("ℹ️ Column 'tier' already exists. Skipping.")
            else:
                log.warning(f"⚠️ Could not add 'tier' column (might already exist): {e}")

        # 2. Reset STATUS Table (Force Schema Re-creation)
        log.info("--- 2. Resetting STATUS Table ---")
        try:
            client.execute("DROP TABLE IF EXISTS gemini_key_status")
            log.info("✅ Dropped old gemini_key_status table.")
        except Exception as e:
            log.error(f"❌ Failed to drop status table: {e}")

        log.info("--- Migration Complete ---")
        log.info("Run the app now. The KeyManager will auto-create the new Status table on init.")

    except Exception as e:
        log.critical(f"❌ Migration Failed: {e}")

if __name__ == "__main__":
    migrate()
