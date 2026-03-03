import os
import sys
import argparse
from datetime import datetime

# Add project root to sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

# 1. First, try to ensure Infisical environment is loaded if possible
try:
    from modules.core import config
except Exception as e:
    print(f"⚠️ Initial import warning: {e}")

from modules.data.db_utils import get_daily_inputs, get_latest_daily_input_date
from modules.ai.ai_services import filter_daily_news_for_macro, filter_daily_news_for_company, summarize_news_with_gemini
from modules.core.logger import AppLogger

def run_test(target_date_str: str, target: str):
    logger = AppLogger("test_getnews")
    target = target.upper()
    
    print(f"--- 🔍 Starting getnews Test Tool for {target} on {target_date_str} ---")
    
    # Check if DB is configured
    from modules.core.config import TURSO_DB_URL, TURSO_AUTH_TOKEN
    if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
        print("❌ CRITICAL: Turso DB credentials not found in config.")
        print("Please ensure INFISICAL_CLIENT_ID, INFISICAL_CLIENT_SECRET, and INFISICAL_PROJECT_ID are set.")
        return

    try:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except ValueError:
        print(f"❌ Invalid date format: {target_date_str}. Use YYYY-MM-DD.")
        return
    
    # 2. Fetch raw news
    print(f"📥 Fetching raw news for {target_date_str}...")
    market_news, _ = get_daily_inputs(target_date)
    
    if not market_news:
        print(f"❌ No news text found for {target_date_str} in the database.")
        return
    
    print(f"✅ Found {len(market_news)} characters of raw news.")
    
    # 3. Filter News
    print(f"🧹 Filtering news for {target}...")
    if target == "MACRO":
        filtered_news = filter_daily_news_for_macro(market_news)
    else:
        # We pass 'technology' as fallback arbitrarily for testing, 
        # actual bot uses company's mapped sector.
        filtered_news = filter_daily_news_for_company(market_news, target, "technology")
    
    if "No specific company or sector news found" in filtered_news or "No macro news found" in filtered_news:
        print(f"⚠️ No specific news found for {target} on {target_date_str}.")
        return
        
    print(f"✅ Filtered news down to {len(filtered_news)} characters.")
    
    # 4. Summarize with Gemini
    print(f"🤖 Calling AI (gemini-3-flash-free) to summarize for {target}...")
    summary = summarize_news_with_gemini(filtered_news, target, logger)
    
    print("\n🚀 --- FINAL SUMMARY OUTPUT ---")
    print(summary)
    print("--------------------------------")

    # 5. CLEANUP: Ensure connections are closed to allow script to exit
    print("\n🧹 Cleaning up connections...")
    try:
        from modules.core.config import infisical_mgr
        if infisical_mgr:
            infisical_mgr.close()
            print("✅ Infisical connection closed.")
            
        from modules.ai.ai_services import KEY_MANAGER
        if KEY_MANAGER:
            KEY_MANAGER.close()
            print("✅ KeyManager connection closed.")
    except Exception as e:
        print(f"⚠️ Cleanup warning: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test the !getnews command logic locally.")
    parser.add_argument("--date", type=str, help="Target date (YYYY-MM-DD). Defaults to latest in DB.")
    parser.add_argument("--target", type=str, default="MACRO", help="Target (MACRO or Company Ticker). Defaults to MACRO.")
    
    args = parser.parse_args()
    
    target_date_str = args.date
    if not target_date_str:
        print("📅 No date provided. Fetching latest news date from DB...")
        target_date_str = get_latest_daily_input_date()
        if not target_date_str:
            print("❌ No news found in the database. DB might be empty or unreachable.")
            sys.exit(1)
        print(f"📅 Using latest date: {target_date_str}")
        
    run_test(target_date_str, args.target)
