
import requests
import json
import logging
import time
import toml
import libsql_client

# Setup Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ErrorProbe")

# 1. READ SECRETS
try:
    with open(".streamlit/secrets.toml", "r") as f:
        secrets = toml.load(f)
        turso_url = secrets["turso"]["db_url"]
        turso_token = secrets["turso"]["auth_token"]
except Exception as e:
    logger.error(f"Failed to read secrets: {e}")
    exit(1)

# 2. GET KEY
try:
    url = turso_url.replace("libsql://", "https://")
    client = libsql_client.create_client_sync(url=url, auth_token=turso_token)
    rs = client.execute("SELECT key_value FROM gemini_api_keys ORDER BY priority LIMIT 1")
    if not rs.rows:
        logger.error("No keys found!")
        exit(1)
    api_key = rs.rows[0][0]
except Exception as e:
    logger.error(f"Failed to get key: {e}")
    exit(1)

def probe_model(model_name, prompt_text, label):
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": prompt_text}]}]}
    headers = {'Content-Type': 'application/json'}
    
    logger.info(f"--- PROBING {model_name.upper()} ({label}) ---")
    start = time.time()
    try:
        resp = requests.post(api_url, headers=headers, json=payload, timeout=60)
        dur = time.time() - start
        
        logger.info(f"Status: {resp.status_code} | Time: {dur:.2f}s")
        if resp.status_code != 200:
            logger.error(f"❌ ERROR HEADER: {dict(resp.headers)}")
            logger.error(f"❌ ERROR BODY: {resp.text}")
        else:
            logger.info("✅ Success")
            
    except Exception as e:
        logger.error(f"Exception: {e}")

# --- TEST 1: 503 ANALYSIS (HEAVY TOKEN LOAD) ---
# Simulating a 15k token EOD prompt
heavy_prompt = "Using the following financial data, analyze the market trends..." + (" data_point " * 15000)
probe_model("gemini-2.5-pro", heavy_prompt, "HEAVY LOAD - 503 CHECK")

# --- TEST 2: 429 ANALYSIS (RAPID FIRE) ---
# Flash models usually have high RPM, but we'll try to hit it
simple_prompt = "Hello"
logger.info("\n--- STARTING RAPID FIRE (429 CHECK) ---")
for i in range(3):
    probe_model("gemini-2.5-flash", simple_prompt, f"RAPID #{i+1}")
    # Don't sleep, try to force rate limit
