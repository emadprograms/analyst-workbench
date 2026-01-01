
import requests
import logging
import toml
import libsql_client
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("FreeTierProbe")

# 1. READ SECRETS
try:
    with open(".streamlit/secrets.toml", "r") as f:
        secrets = toml.load(f)
        turso_url = secrets["turso"]["db_url"]
        turso_token = secrets["turso"]["auth_token"]
except Exception as e:
    exit(1)

# 2. GET FREE KEY
try:
    url = turso_url.replace("libsql://", "https://")
    client = libsql_client.create_client_sync(url=url, auth_token=turso_token)
    
    # List all keys
    rs_all = client.execute("SELECT key_name, tier FROM gemini_api_keys")
    print("--- KEY INVENTORY ---")
    for row in rs_all.rows:
        print(f"Key: {row[0]} | Tier: {row[1]}")
    
    # Get a specific Free key
    rs = client.execute("SELECT key_value FROM gemini_api_keys WHERE tier='free' LIMIT 1")
    if not rs.rows:
        logger.error("No FREE keys found!")
        exit(1)
    free_key = rs.rows[0][0]
    logger.info(f"Using Free Key: {free_key[:5]}...")
except Exception as e:
    exit(1)

# 3. TEST RAPID FIRE ON FREE KEY
def probe(model, key):
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    try:
        resp = requests.post(api_url, 
            json={"contents": [{"parts": [{"text": "Hello"}]}]},
            headers={'Content-Type': 'application/json'}
        )
        if resp.status_code == 429:
            logger.error(f"❌ 429 HIT! Header: {resp.headers.get('Retry-After', 'No-Header')}")
            logger.error(f"Body: {resp.text}")
        elif resp.status_code == 200:
            logger.info("✅ Success")
        else:
            logger.info(f"Status: {resp.status_code}")
    except: pass

logger.info("\n--- RAPID FIRE FREE TIER ---")
for i in range(5):
    probe("gemini-2.5-flash", free_key)
    # No sleep
