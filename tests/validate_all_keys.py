
import requests
import logging
import toml
import libsql_client
import time

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("KeyValidator")

# 1. READ SECRETS
try:
    with open(".streamlit/secrets.toml", "r") as f:
        secrets = toml.load(f)
        turso_url = secrets["turso"]["db_url"]
        turso_token = secrets["turso"]["auth_token"]
except Exception as e:
    logger.error("Failed to read secrets.")
    exit(1)

# 2. GET ALL KEYS
try:
    url = turso_url.replace("libsql://", "https://")
    client = libsql_client.create_client_sync(url=url, auth_token=turso_token)
    rs = client.execute("SELECT key_name, key_value, tier FROM gemini_api_keys ORDER BY tier DESC, key_name ASC")
    keys = [(r[0], r[1], r[2]) for r in rs.rows]
except Exception as e:
    logger.error(f"DB Error: {e}")
    exit(1)

print(f"--- VALIDATING {len(keys)} KEYS ---")

results = {"OK": 0, "429": 0, "INVALID": 0, "OTHER": 0}

for name, key, tier in keys:
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
    try:
        start = time.time()
        resp = requests.post(api_url, 
            json={"contents": [{"parts": [{"text": "Hi"}]}]},
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        dur = time.time() - start
        
        status = resp.status_code
        if status == 200:
            print(f"✅ {name:<20} | {tier.upper():<5} | OK ({dur:.2f}s)")
            results["OK"] += 1
        elif status == 429:
            print(f"⚠️ {name:<20} | {tier.upper():<5} | 429 RATE LIMIT")
            results["429"] += 1
        elif status == 400 and "API key not valid" in resp.text:
            print(f"❌ {name:<20} | {tier.upper():<5} | INVALID KEY")
            results["INVALID"] += 1
        else:
            print(f"❓ {name:<20} | {tier.upper():<5} | {status} {resp.reason}")
            results["OTHER"] += 1
            
    except Exception as e:
        print(f"💥 {name:<20} | {tier.upper():<5} | EXCEPTION: {e}")

print("\n--- SUMMARY ---")
print(f"Valid: {results['OK']}")
print(f"Rate Limited: {results['429']}")
print(f"Invalid: {results['INVALID']}")
print(f"Other: {results['OTHER']}")
