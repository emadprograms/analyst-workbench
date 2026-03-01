import json
from modules.data.db_utils import get_table_data
from modules.analysis.impact_engine import get_or_compute_context

def main():
    date = '2026-02-13'
    tickers = ['AAPL', 'AMZN', 'GOOGL', 'NVDA', 'TSLA']
    
    df = get_table_data('aw_company_cards')
    df_filtered = df[df['date'] == date]
    
    print(f"--- FACT CHECK REPORT FOR {date} ---\n")
    
    for ticker in tickers:
        print(f"=== {ticker} ===")
        # Get AI Output
        row = df_filtered[df_filtered['ticker'] == ticker]
        if row.empty:
            print("No card found.")
            continue
            
        card = json.loads(row.iloc[0]['company_card_json'])
        ai_action = next((entry['action'] for entry in card.get('technicalStructure', {}).get('keyActionLog', []) if entry['date'] == date), None)
        ai_plans = f"Plan A: {card.get('openingTradePlan', {}).get('planName')}\nPlan B: {card.get('alternativePlan', {}).get('planName')}"
        ai_pattern = card.get('technicalStructure', {}).get('pattern')
        
        # Get Ground Truth (Impact Engine)
        # Note: We need a mock logger or just pass None if impact_engine allows it
        class MockLogger:
            def log(self, *args, **kwargs): pass
            def warning(self, *args, **kwargs): pass
            
        try:
            # We don't have db conn here easily, so we just read the cache file directly
            with open(f"cache/context/{ticker}_{date}.json", "r") as f:
                truth = json.load(f)
                
            actual_open = truth.get('sessions', {}).get('pre_market', {}).get('open', 'N/A')
            actual_high = truth.get('meta', {}).get('high', 'N/A')
            actual_low = truth.get('meta', {}).get('low', 'N/A')
            actual_close = truth.get('meta', {}).get('close', 'N/A')
            actual_poc = truth.get('levels', {}).get('poc', 'N/A')
            actual_vah = truth.get('levels', {}).get('vah', 'N/A')
            actual_val = truth.get('levels', {}).get('val', 'N/A')
            
            print("--- GROUND TRUTH (Impact Math) ---")
            print(f"O: {actual_open} | H: {actual_high} | L: {actual_low} | C: {actual_close}")
            print(f"POC: {actual_poc} | VAH: {actual_vah} | VAL: {actual_val}")
            
            print("\n--- AI OUTPUT ---")
            print(f"todaysAction:\n{ai_action}")
            print(f"Pattern:\n{ai_pattern}")
            print(f"Plans:\n{ai_plans}")
            
            print("\n")
            
        except Exception as e:
            print(f"Could not load truth data: {e}\n")

if __name__ == "__main__":
    main()
