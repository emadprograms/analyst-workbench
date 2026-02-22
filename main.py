from __future__ import annotations
import argparse
import sys
import os
from datetime import date
from modules.core.config import ALL_TICKERS, STOCK_TICKERS, ETF_TICKERS, AVAILABLE_MODELS, DISCORD_WEBHOOK_URL
from modules.core.logger import AppLogger
from modules.data.db_utils import (
    get_daily_inputs, 
    upsert_daily_inputs, 
    get_economy_card, 
    upsert_economy_card,
    get_company_card_and_notes,
    upsert_company_card,
    get_all_tickers_from_db
)
from modules.ai.ai_services import update_economy_card, update_company_card
from modules.data.data_processing import generate_analysis_text

def run_update_economy(selected_date: date, model_name: str, logger: AppLogger):
    logger.log(f"🧠 Updating Economy Card for {selected_date}...")
    
    # 1. Get Market News
    market_news, _ = get_daily_inputs(selected_date)
    if not market_news:
        logger.error(f"No market news found for {selected_date}. Economy update skipped.")
        from modules.ai.ai_services import TRACKER
        TRACKER.log_call(0, False, model_name, ticker="ECONOMY", error="No market news found")
        return

    # 2. Get Current Card
    current_eco_json, _ = get_economy_card(before_date=selected_date.isoformat())
    
    # 3. Generate ETF Summaries (The 'Evidence')
    logger.log("   Fetching ETF intraday analysis...")
    etf_summaries = generate_analysis_text(list(ETF_TICKERS), selected_date)
    
    # 4. Update via AI
    new_eco_json = update_economy_card(
        current_economy_card=current_eco_json,
        daily_market_news=market_news,
        model_name=model_name,
        etf_summaries=etf_summaries,
        selected_date=selected_date,
        logger=logger
    )
    
    # 5. Save
    if new_eco_json:
        success = upsert_economy_card(selected_date, etf_summaries, new_eco_json)
        if success:
            logger.log(f"✅ Economy Card updated for {selected_date}")
        else:
            logger.error("❌ Failed to save Economy Card to DB")
            from modules.ai.ai_services import TRACKER
            TRACKER.log_call(0, False, model_name, ticker="ECONOMY", error="DB Save Failed")
    else:
        logger.error("❌ AI failed to generate new Economy Card")
        # TRACKER.log_call handled inside update_economy_card failure paths

def run_pipeline(selected_date: date, model_name: str, logger: AppLogger):
    logger.log(f"🚀 Starting Full Pipeline for {selected_date} using {model_name}")

    # 1. Update Economy Card
    run_update_economy(selected_date, model_name, logger)

    # 2. Update Company Cards
    logger.log("--- Updating Company Cards ---")
    tickers = get_all_tickers_from_db()
    if not tickers:
        logger.log("⚠️ No tickers found in 'stocks' table. Using config fallback.")
        tickers = STOCK_TICKERS

    for ticker in tickers:
        logger.log(f"Processing {ticker}...")
        prev_card, hist_notes, prev_date = get_company_card_and_notes(ticker, selected_date)
        
        # In CLI, we'd ideally fetch the ticker summary from DB or generate it.
        # For now, we'll placeholder the summary input as we move towards full automation
        ticker_summary = f"CLI Update for {ticker} on {selected_date}" 
        
        # market_news for context
        market_news, _ = get_daily_inputs(selected_date)

        new_card = update_company_card(
            ticker=ticker,
            current_company_card=prev_card,
            prev_update_date=prev_date,
            historical_notes=hist_notes,
            ticker_summary=ticker_summary,
            selected_date=selected_date,
            model_name=model_name,
            daily_market_news=market_news,
            logger=logger
        )
        
        if new_card:
            upsert_company_card(selected_date, ticker, ticker_summary, new_card)
        else:
            from modules.ai.ai_services import TRACKER
            TRACKER.log_call(0, False, model_name, ticker=ticker, error="Update Failed")
    
    logger.log("✅ Full Pipeline run complete.")

def main():
    parser = argparse.ArgumentParser(description="Analyst Workbench CLI")
    parser.add_argument("--date", type=str, help="Target date (YYYY-MM-DD), defaults to today", default=date.today().isoformat())
    parser.add_argument(
        "--model", 
        type=str, 
        help=f"Gemini model name. Options: {', '.join(AVAILABLE_MODELS.keys())}", 
        default="gemini-2.0-flash-paid",
        choices=list(AVAILABLE_MODELS.keys())
    )
    parser.add_argument("--action", choices=["run", "update-economy", "input-news", "inspect", "setup"], default="run", help="Action to perform")
    parser.add_argument("--text", type=str, help="Market news text (used with --action input-news)")
    parser.add_argument("--file", type=str, help="Path to a text file containing market news (used with --action input-news)")
    parser.add_argument("--webhook", type=str, help="Optional Discord Webhook URL for reporting")
    
    args = parser.parse_args()
    logger = AppLogger()

    try:
        target_date = date.fromisoformat(args.date)
    except ValueError:
        logger.error(f"Invalid date format: {args.date}. Use YYYY-MM-DD.")
        sys.exit(1)

    from modules.core.config import infisical_mgr
    from modules.ai.ai_services import TRACKER
    import requests

    def send_webhook_report(webhook_url, target_date):
        if not webhook_url: return
        TRACKER.finish()
        embeds = TRACKER.get_discord_embeds(target_date.isoformat())
        try:
            requests.post(webhook_url, json={"embeds": embeds}, timeout=10)
        except Exception as e:
            logger.error(f"Failed to send Discord webhook: {e}")

    TRACKER.start()
    try:
        if args.action == "run":
            run_pipeline(target_date, args.model, logger)
        elif args.action == "update-economy":
            run_update_economy(target_date, args.model, logger)
        elif args.action == "input-news":
            news_content = None
            if args.text:
                news_content = args.text
            elif args.file:
                try:
                    with open(args.file, 'r') as f:
                        news_content = f.read()
                except Exception as e:
                    logger.error(f"Failed to read file {args.file}: {e}")
                    sys.exit(1)
            
            if not news_content:
                logger.error("You must provide news content via --text or --file when using --action input-news")
                sys.exit(1)
            
            if upsert_daily_inputs(target_date, news_content):
                logger.log(f"✅ Market news successfully saved for {target_date}")
            else:
                logger.error(f"❌ Failed to save market news for {target_date}")
        elif args.action == "inspect":
            from modules.data.inspect_db import inspect
            inspect()
        elif args.action == "setup":
            from modules.data.setup_db import create_tables
            create_tables()
        
        # Send Report if webhook exists
        webhook = getattr(args, 'webhook', None) or DISCORD_WEBHOOK_URL
        if webhook:
            send_webhook_report(webhook, target_date)

    finally:
        # 6. Cleanup Resources
        if 'infisical_mgr' in locals() or 'infisical_mgr' in globals():
            infisical_mgr.close()
        
        # 7. Force Exit
        # os._exit(0) is used to ensure the process dies immediately,
        # preventing hangs from unclosed background sessions/threads.
        os._exit(0)

if __name__ == "__main__":
    main()
