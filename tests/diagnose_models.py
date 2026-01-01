
import os
import toml
import requests
import json
import logging
import libsql_client

# Setup Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DiagnoseModels")

# 1. READ SECRETS
try:
    with open(".streamlit/secrets.toml", "r") as f:
        secrets = toml.load(f)
        turso_url = secrets["turso"]["db_url"]
        turso_token = secrets["turso"]["auth_token"]
except Exception as e:
    logger.error(f"Failed to read secrets: {e}")
    exit(1)

# 2. GET KEY FROM DB
try:
    url = turso_url.replace("libsql://", "https://")
    client = libsql_client.create_client_sync(url=url, auth_token=turso_token)
    rs = client.execute("SELECT key_value, tier FROM gemini_api_keys ORDER BY priority LIMIT 1")
    if not rs.rows:
        logger.error("No keys found in Turso DB!")
        exit(1)
    
    api_key = rs.rows[0][0]
    tier = rs.rows[0][1]
    logger.info(f"Using API Key (Tier: {tier}): {api_key[:5]}...")
except Exception as e:
    logger.error(f"Failed to get key from DB: {e}")
    exit(1)

# 3. LIST MODELS
logger.info("--- LISTING AVAILABLE MODELS ---")
list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
try:
    resp = requests.get(list_url)
    if resp.status_code == 200:
        models = resp.json().get("models", [])
        model_names = [m["name"].split("/")[-1] for m in models]
        print("\n".join(model_names))
        
        # Check for specific ones
        print("\n--- CHECKING SPECIFIC MODELS ---")
        targets = ["gemini-2.5-pro", "gemini-3-pro-preview", "gemini-2.0-flash", "gemini-1.5-pro"]
        for t in targets:
            if t in model_names:
                print(f"✅ {t} FOUND")
            else:
                print(f"❌ {t} NOT FOUND")
    else:
        logger.error(f"Failed to list models: {resp.status_code} {resp.text}")
except Exception as e:
    logger.error(f"Exception listing models: {e}")

# 4. TEST GEMINI-2.5-PRO (if failed above, try direct call anyway to see error body)
logger.info("\n--- TEST CALL TO GEMINI-2.5-PRO ---")
target_model = "gemini-2.5-pro" # The one causing 503
test_url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent?key={api_key}"
payload = {
    "contents": [{"parts": [{"text": "Hello"}]}]
}
try:
    resp = requests.post(test_url, json=payload, headers={"Content-Type": "application/json"})
    logger.info(f"Status: {resp.status_code}")
    print(resp.text)
except Exception as e:
    logger.error(f"Exception calling model: {e}")
