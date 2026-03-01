from __future__ import annotations
import os
import sys
import argparse
import time
import json
import concurrent.futures
from datetime import date
import requests

def send_webhook_report(webhook_url, target_date, action, model, logger=None):
    """Sends the execution summary and optional log file to Discord."""
    if not webhook_url: return
    
    from modules.ai.ai_services import TRACKER
    TRACKER.finish()
    embeds = TRACKER.get_discord_embeds(target_date.isoformat())
    
    # Dashboard is always the first embed in the list
    payload = {"embeds": embeds}
    files = {}
    
    # 1. Enhance Log Filename
    # Format: {descriptive_action}_{date}_{model}_{timestamp}.log
    action_map = {
        "update-economy": "Economy_Card_Update",
        "update-company": "Company_Card_Update",
        "view-economy": "View_Economy_Card",
        "view-company": "View_Company_Card",
        "input-news": "Market_News_Input",
        "inspect": "DB_Inspection",
        "setup": "DB_Setup",
        "check-news": "News_Check",
        "test-webhook": "Webhook_Test"
    }
    desc_action = action_map.get(action, action).replace("-", "_")
    
    # Only show model if it's an AI-heavy action
    ai_actions = ["update-economy", "update-company"]
    model_display = model if action in ai_actions else "No_AI_Used"
    
    timestamp = time.strftime("%H%M%S")
    log_filename = f"{desc_action}_{target_date.isoformat()}_{model_display}_{timestamp}.log"
    
    # 2. Attach the captured logs as a file
    if logger and hasattr(logger, 'get_full_log'):
        log_content = logger.get_full_log()
        if log_content:
            files["file"] = (log_filename, log_content, "text/plain")
    
    # 3. Attach generated cards (artifacts)
    if hasattr(TRACKER.metrics, 'artifacts'):
        for name, content in TRACKER.metrics.artifacts.items():
            # Discord limit is 10 files per message
            if len(files) >= 10: break
            files[name] = (f"{name}.json", content, "application/json")
    
    try:
        # --- MESSAGES 1 to N: The Embeds (Dashboard, Quality Table, etc.) ---
        # We send these sequentially to ensure they appear in order.
        # Discord allows up to 10 embeds per message, but we send them separately
        # to ensure we never hit the 2000-character-per-message limit when tables are large.
        for i, embed in enumerate(embeds):
            requests.post(webhook_url, json={"embeds": [embed]}, timeout=15)
            # Brief pause to help Discord ordering
            if len(embeds) > 1:
                time.sleep(0.5)

        # --- FINAL MESSAGE: The Files (Logs & Cards) ---
        # Skip sending logs for input-news and inspect to keep feed clean, but KEEP for check-news
        skip_files_actions = ["input-news", "inspect"]
        if files and action not in skip_files_actions:
            # We send a small follow-up message with the files
            requests.post(
                webhook_url, 
                data={"content": "📁 **Attached Logs & Generated Cards:**"}, 
                files=files, 
                timeout=30
            )
    except Exception as e:
        if logger:
            logger.error(f"Failed to send Discord webhook: {e}")
        else:
            print(f"ERROR: Failed to send Discord webhook: {e}")

from modules.core.config import ALL_TICKERS, STOCK_TICKERS, ETF_TICKERS, AVAILABLE_MODELS, DISCORD_WEBHOOK_URL
from modules.core.logger import AppLogger
from modules.data.db_utils import (
    get_daily_inputs,
    upsert_daily_inputs,
    get_economy_card,
    upsert_economy_card,
    get_company_card_and_notes,
    upsert_company_card,
    get_all_tickers_from_db,
    get_archived_economy_card
)
from modules.ai.ai_services import update_economy_card, update_company_card
from modules.analysis.impact_engine import get_latest_price_details

def run_update_economy(selected_date: date, model_name: str, logger: AppLogger) -> bool:
    logger.log(f"🧠 Updating Economy Card for {selected_date}...")
    
    # 1. Get Market News
    market_news, _ = get_daily_inputs(selected_date)
    if not market_news:
        err_msg = f"No market news found for {selected_date} in 'aw_daily_news'. Pipeline Halted."
        logger.error(err_msg)
        from modules.ai.ai_services import TRACKER
        TRACKER.log_error("ECONOMY", err_msg)
        return False

    # 2. Get Current Card
    current_eco_json, _ = get_economy_card(before_date=selected_date.isoformat())
    
    # 3. Check for Data Availability
    logger.log("   Verifying market data availability...")
    cutoff_str = f"{selected_date.isoformat()} 23:59:59"
    close_price, ts = get_latest_price_details(None, "SPY", cutoff_str, logger)
    if not ts or not ts.startswith(selected_date.isoformat()):
        err_msg = f"Market data missing for {selected_date} in Price DB. Pipeline Halted."
        logger.error(err_msg)
        from modules.ai.ai_services import TRACKER
        TRACKER.log_error("ECONOMY", err_msg)
        return False

    # 4. Update via AI
    new_eco_json = update_economy_card(
        current_economy_card=current_eco_json,
        daily_market_news=market_news,
        model_name=model_name,
        selected_date=selected_date,
        logger=logger
    )
    
    # 5. Save
    if new_eco_json:
        # Use a placeholder summary since we now store this purely in JSON
        success = upsert_economy_card(selected_date, "Evidence processed via Impact Engine", new_eco_json)
        if success:
            logger.log(f"✅ Economy Card updated for {selected_date}")
            return True
        else:
            logger.error("❌ Failed to save Economy Card to DB")
            from modules.ai.ai_services import TRACKER
            TRACKER.log_error("ECONOMY", "DB Save Failed")
            return False
    else:
        logger.error("❌ AI failed to generate new Economy Card")
        return False

def run_update_company(selected_date: date, model_name: str, tickers: list[str], logger: AppLogger) -> bool:
    logger.log(f"🧠 Updating Company Cards for {len(tickers)} tickers on {selected_date}...")

    # 1. Get Market News for context
    market_news, _ = get_daily_inputs(selected_date)
    if not market_news:
        logger.warning(f"⚠️ No market news found for {selected_date}. Continuing without macro context.")

    # 2. Get Economy Card for context
    economy_card_json, _ = get_archived_economy_card(selected_date)
    if not economy_card_json:
        logger.error(f"❌ No Economy Card found for {selected_date}. Please run economy card update first. Pipeline Halted.")
        return False

    def process_ticker(ticker):
        logger.log(f"Processing {ticker}...")
        prev_card, hist_notes, prev_date = get_company_card_and_notes(ticker, selected_date)

        # Generate ticker summary (Evidence)
        # Note: In the future, this might be more sophisticated
        ticker_summary = f"CLI Update for {ticker} on {selected_date}"

        new_card = update_company_card(
            ticker=ticker,
            previous_card_json=prev_card,
            previous_card_date=prev_date,
            historical_notes=hist_notes,
            new_eod_date=selected_date,
            model_name=model_name,
            market_context_summary=market_news,
            economy_card_json=economy_card_json,
            logger=logger
        )        
        if new_card:
            if upsert_company_card(selected_date, ticker, ticker_summary, new_card):
                return True
            else:
                logger.error(f"❌ Failed to save {ticker} card to DB")
                return False
        else:
            # Note: call_gemini_api already tracks failures internally via TRACKER.
            # Only log a non-API error here if the card update failed for non-API reasons.
            logger.error(f"❌ AI update failed for {ticker}")
            return False

    # Determine max_workers based on available keys for this model's tier
    from modules.ai.ai_services import KEY_MANAGER
    from modules.core.key_manager import KeyManager
    
    max_concurrent = 19  # default cap
    if KEY_MANAGER:
        tier = KeyManager.MODELS_CONFIG.get(model_name, {}).get('tier', 'free')
        key_count = KEY_MANAGER.get_tier_key_count(tier)
        max_concurrent = max(1, min(key_count, 5))
        logger.log(f"🔑 {key_count} {tier}-tier key(s) available → max_workers={max_concurrent}")

    success_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(tickers), max_concurrent)) as executor:
        results = list(executor.map(process_ticker, tickers))
        success_count = sum(1 for r in results if r)
    
    logger.log(f"✅ Company Card updates complete. Success: {success_count}/{len(tickers)}")
    return success_count > 0

def main():
    parser = argparse.ArgumentParser(description="Analyst Workbench CLI")
    parser.add_argument("--date", type=str, help="Target date (YYYY-MM-DD)")
    parser.add_argument("--model", 
        type=str, 
        help=f"Gemini model name. Options: {', '.join(AVAILABLE_MODELS.keys())}", 
        default="gemini-3-flash-free",
        choices=list(AVAILABLE_MODELS.keys())
    )
    parser.add_argument("--action", choices=["update-economy", "update-company", "input-news", "inspect", "setup", "test-webhook", "check-news"], default="update-economy", help="Action to perform")
    parser.add_argument("--tickers", type=str, help="Comma-separated list of tickers (used with --action update-company)")
    parser.add_argument("--text", type=str, help="Market news text (used with --action input-news)")
    
    parser.add_argument("--file", type=str, help="Path to a text file containing market news (used with --action input-news)")
    parser.add_argument("--url", type=str, help="URL to a text file containing market news (used with --action input-news)")
    parser.add_argument("--webhook", type=str, help="Optional Discord Webhook URL for reporting")
    
    args = parser.parse_args()
    logger = AppLogger()
    exit_code = 0  # Track exit status for GitHub Actions

    # Define TRACKER here to ensure it's available in global catch
    from modules.ai.ai_services import TRACKER
    from modules.core.config import infisical_mgr

    target_date = None  # Initialize to prevent UnboundLocalError in finally
    try:
        # Default date logic
        date_input = args.date or date.today().isoformat()
        try:
            target_date = date.fromisoformat(date_input)
        except ValueError:
            logger.error(f"Invalid date format: {date_input}. Use YYYY-MM-DD.")
            exit_code = 1
            return # Skip to finally

        # Map actions to descriptive names
        action_map = {
            "update-economy": "Economy_Card_Update",
            "update-company": "Company_Card_Update",
            "input-news": "Market_News_Input",
            "inspect": "DB_Inspection",
            "setup": "DB_Setup",
            "check-news": "News_Check",
            "test-webhook": "Webhook_Test"
        }
        desc_action = action_map.get(args.action, args.action).replace("-", "_")

        TRACKER.start(action_type=desc_action)
        
        # --- NEW: Mandatory Date Enforcement for specific actions ---
        if args.action == "inspect" and not args.date:
             logger.error("🛑 Action 'inspect' requires a specific --date. It will not default to today.")
             exit_code = 1
             return

        if args.action == "update-economy":
            if not run_update_economy(target_date, args.model, logger):
                exit_code = 1
        elif args.action == "update-company":
            if not args.tickers:
                logger.error("🛑 Action 'update-company' requires --tickers.")
                exit_code = 1
                return
            raw_tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
            # Expand "ALL" keyword to all stock tickers from DB or config
            if raw_tickers == ["ALL"]:
                db_tickers = get_all_tickers_from_db()
                etf_set = set(ETF_TICKERS)
                ticker_list = [t for t in db_tickers if t not in etf_set] or STOCK_TICKERS
                logger.log(f"📋 Expanded 'all' to {len(ticker_list)} stock tickers: {', '.join(ticker_list)}")
            else:
                ticker_list = raw_tickers
            if not run_update_company(target_date, args.model, ticker_list, logger):
                exit_code = 1
        elif args.action == "input-news":
            news_content = None
            if args.text:
                news_content = args.text
            elif args.file:
                try:
                    with open(args.file, 'r') as f:
                        news_content = f.read()
                except Exception as e:
                    logger.error(f"Failed to read local file {args.file}: {e}")
                    raise
            elif args.url:
                try:
                    logger.log(f"🌐 Downloading news from URL: {args.url[:50]}...")
                    resp = requests.get(args.url, timeout=30)
                    logger.log(f"   Response Status: {resp.status_code}")
                    resp.raise_for_status()
                    news_content = resp.text
                    logger.log(f"✅ Downloaded {len(news_content)} characters.")
                    if news_content:
                        logger.log(f"   Preview Start: {news_content[:50]!r}")
                        logger.log(f"   Preview End: {news_content[-50:]!r}")
                except Exception as e:
                    logger.error(f"❌ Failed to download news from URL: {e}")
                    TRACKER.log_error("NEWS_DOWNLOAD", str(e))
                    raise
            
            if not news_content:
                logger.error("No news content provided (text, file, or url).")
                TRACKER.log_error("NEWS_INPUT", "Empty news content")
                exit_code = 1
            else:
                logger.log(f"💾 Upserting news to Turso for {target_date}...")
                if upsert_daily_inputs(target_date, news_content):
                    char_count = len(news_content)
                    logger.log(f"✅ Market news successfully saved for {target_date}")
                    TRACKER.metrics.details.append(f"✅ News Saved: {target_date} ({char_count:,} chars)")
                    TRACKER.metrics.success_count += 1
                else:
                    logger.error(f"❌ Database upsert FAILED for {target_date}")
                    TRACKER.log_error("NEWS_SAVE", "DB UPSERT failed")
                    exit_code = 1
        elif args.action == "inspect":
            from modules.data.inspect_db import inspect
            inspect(target_date, logger=logger)
        elif args.action == "setup":
            from modules.data.setup_db import create_tables
            create_tables()
        elif args.action == "check-news":
            market_news, _ = get_daily_inputs(target_date)
            if market_news:
                char_count = len(market_news)
                logger.log(f"\n✅ NEWS FOUND for {target_date} ({char_count} chars):\n{'-'*40}\n{market_news[:500]}...\n{'-'*40}")
                TRACKER.set_result("news_status", f"✅ Found ({char_count} chars)")
            else:
                logger.error(f"❌ NO NEWS FOUND for {target_date}")
                TRACKER.set_result("news_status", "❌ Not Found")
        elif args.action == "test-webhook":
            logger.log("🧪 Sending a test Discord notification...")
            TRACKER.log_call(100, True, "Test-Model", ticker="TEST-TICKER")
            
    except Exception as e:
        logger.error(f"💥 Fatal Exception in main pipeline: {e}")
        import traceback
        logger.error(traceback.format_exc())
        exit_code = 1
    finally:
        # Final Reporting
        webhook = getattr(args, 'webhook', None) or DISCORD_WEBHOOK_URL
        if webhook and target_date is not None:
            try:
                # Always finish tracker before report
                send_webhook_report(webhook, target_date, args.action, args.model, logger=logger)
            except Exception as report_err:
                print(f"CRITICAL: Failed to send final Discord report: {report_err}")

        # Cleanup
        if 'infisical_mgr' in locals() or 'infisical_mgr' in globals():
            try:
                infisical_mgr.close()
            except: pass
        
        # Force Exit with status code to prevent hangs and report correctly to GitHub
        logger.log(f"System Exit with code: {exit_code}")
        os._exit(exit_code)

if __name__ == "__main__":
    main()
