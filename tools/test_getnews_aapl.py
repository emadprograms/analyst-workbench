import os
import sys
from datetime import datetime

# Add project root to sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

# 1. First, try to ensure Infisical environment is loaded if possible
# We'll just import config which does this automatically
try:
    from modules.core import config
    from modules.ai import ai_services
except Exception as e:
    print(f"⚠️ Initial import warning: {e}")

from modules.data.db_utils import get_daily_inputs, get_latest_daily_input_date
from modules.ai.ai_services import filter_daily_news_for_company, summarize_news_with_gemini
from modules.core.logger import AppLogger

def run_test():
    logger = AppLogger("test_getnews")
    ticker = "AAPL"
    
    print("--- 🔍 Starting getnews Test Tool ---")
    
    # Check if DB is configured
    from modules.core.config import TURSO_DB_URL, TURSO_AUTH_TOKEN
    if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
        print("❌ CRITICAL: Turso DB credentials not found in config.")
        print("Please ensure INFISICAL_CLIENT_ID, INFISICAL_CLIENT_SECRET, and INFISICAL_PROJECT_ID are set.")
        return

    # 1. Get the latest date with news
    print("📅 Fetching latest news date from DB...")
    latest_date_str = get_latest_daily_input_date()
    if not latest_date_str:
        print("❌ No news found in the database. DB might be empty or unreachable.")
        return
        
    print(f"📅 Latest news date in DB: {latest_date_str}")
    target_date = datetime.strptime(latest_date_str, "%Y-%m-%d").date()
    
    # 2. Fetch raw news
    print(f"📥 Fetching raw news for {latest_date_str}...")
    market_news, _ = get_daily_inputs(target_date)
    
    if not market_news:
        print(f"❌ No news text found for {latest_date_str} despite date being present.")
        return
    
    print(f"✅ Found {len(market_news)} characters of raw news.")
    
    # 3. Filter for AAPL
    print(f"🧹 Filtering news for {ticker} (and Technology sector)...")
    # We pass 'technology' as fallback
    filtered_news = filter_daily_news_for_company(market_news, ticker, "technology")
    
    if "No specific company or sector news found" in filtered_news:
        print(f"⚠️ No specific news found for {ticker} or Tech sector on {latest_date_str}.")
        return
        
    print(f"✅ Filtered news down to {len(filtered_news)} characters.")
    
    # 4. Summarize with Gemini
    print(f"🤖 Calling AI (gemini-3-flash-free) to summarize for {ticker}...")
    summary = summarize_news_with_gemini(filtered_news, ticker, logger)
    
    print("\n🚀 --- FINAL SUMMARY OUTPUT ---")
    print(summary)
    print("--------------------------------")

    # 5. CLEANUP: Ensure connections are closed to allow script to exit
    print("🧹 Cleaning up connections...")
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
    run_test()
