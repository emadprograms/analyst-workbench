from __future__ import annotations
import argparse
import sys
from datetime import date
from modules.core.config import ALL_TICKERS, STOCK_TICKERS, ETF_TICKERS
from modules.core.logger import AppLogger
from modules.data.db_utils import (
    get_daily_inputs, 
    upsert_daily_inputs, 
    get_economy_card, 
    get_company_card_and_notes
)
from modules.ai.ai_services import update_economy_card, update_company_card
from modules.data.data_processing import parse_raw_summary

def run_pipeline(selected_date: date, model_name: str, logger: AppLogger):
    logger.log(f"🚀 Starting Pipeline for {selected_date} using {model_name}")

    # 1. Get Market News
    market_news, _ = get_daily_inputs(selected_date)
    if not market_news:
        logger.error(f"No market news found for {selected_date}. Please add it to the database first.")
        return

    # 2. Update Economy Card
    logger.log("--- Updating Economy Card ---")
    current_eco_json, _ = get_economy_card(before_date=selected_date.isoformat())
    # Note: In a real CLI flow, you might want to fetch ETF summaries first.
    # The original app.py called generate_analysis_text for ETFs.
    # For now, we'll assume the data is there or simplified.
    # etf_summaries = generate_analysis_text(ETF_TICKERS, selected_date)
    # new_eco_json = update_economy_card(current_eco_json, market_news, model_name, etf_summaries, selected_date, logger)
    # ... logic here to save ...
    logger.log("Economy Card update triggered (Placeholder logic - needs ETF summaries bridge)")

    # 3. Update Company Cards
    logger.log("--- Updating Company Cards ---")
    for ticker in STOCK_TICKERS:
        logger.log(f"Processing {ticker}...")
        prev_card, hist_notes, prev_date = get_company_card_and_notes(ticker, selected_date)
        # new_card = update_company_card(ticker, prev_card, prev_date, hist_notes, "Summary here", selected_date, model_name, market_news, logger)
        # ... logic here to save ...
    
    logger.log("✅ Pipeline run complete.")

def main():
    parser = argparse.ArgumentParser(description="Analyst Workbench CLI")
    parser.add_argument("--date", type=str, help="Target date (YYYY-MM-DD), defaults to today", default=date.today().isoformat())
    parser.add_argument("--model", type=str, help="Gemini model name", default="gemini-2.5-flash-lite-free")
    parser.add_argument("--action", choices=["run", "inspect", "setup"], default="run", help="Action to perform")
    
    args = parser.parse_args()
    logger = AppLogger()

    try:
        target_date = date.fromisoformat(args.date)
    except ValueError:
        logger.error(f"Invalid date format: {args.date}. Use YYYY-MM-DD.")
        sys.exit(1)

    if args.action == "run":
        run_pipeline(target_date, args.model, logger)
    elif args.action == "inspect":
        from modules.data.inspect_db import inspect
        inspect()
    elif args.action == "setup":
        from modules.data.setup_db import create_tables
        create_tables()

if __name__ == "__main__":
    main()
