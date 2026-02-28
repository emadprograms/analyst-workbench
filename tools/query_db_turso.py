import json
from modules.data.db_utils import get_table_data

def main():
    try:
        df = get_table_data('aw_company_cards')
        if df.empty:
            print("Table aw_company_cards is empty or could not be loaded.")
            return

        # Filter for date = '2026-02-13'
        df_filtered = df[df['date'] == '2026-02-13']
        if df_filtered.empty:
            print("No records found for date '2026-02-13'.")
            return

        for index, row in df_filtered.iterrows():
            ticker = row['ticker']
            card_json_str = row['company_card_json']
            try:
                card = json.loads(card_json_str)
                
                plan_a = card.get('openingTradePlan', {})
                plan_b = card.get('alternativePlan', {})
                
                print(f"--- TICKER: {ticker} ---")
                print(f"Plan A (Primary): {plan_a.get('planName')} | Trigger: {plan_a.get('trigger')}")
                print(f"Plan B (Alternative): {plan_b.get('planName')} | Trigger: {plan_b.get('trigger')}")
                print()
                
            except Exception as e:
                print(f"Error parsing {ticker}: {e}\n")

    except Exception as e:
        print(f"Fatal error: {e}")

if __name__ == "__main__":
    main()