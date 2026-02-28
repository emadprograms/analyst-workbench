import json
from modules.data.db_utils import get_table_data

def main():
    try:
        df = get_table_data('aw_company_cards')
        if df.empty:
            print("Table aw_company_cards is empty or could not be loaded.")
            return

        # Find the most recent date
        latest_date = df['date'].max()
        print(f"Latest Date in DB: {latest_date}")
        
        df_filtered = df[df['date'] == latest_date]

        for index, row in df_filtered.iterrows():
            ticker = row['ticker']
            card_json_str = row['company_card_json']
            try:
                card = json.loads(card_json_str)
                
                # Check todaysAction
                key_action_log = card.get('technicalStructure', {}).get('keyActionLog', [])
                todays_action = next((entry['action'] for entry in key_action_log if entry['date'] == latest_date), None)
                
                # Check plans
                plan_a = card.get('openingTradePlan', {})
                plan_b = card.get('alternativePlan', {})
                
                print(f"--- TICKER: {ticker} ---")
                if todays_action:
                    print(f"todaysAction (len {len(todays_action)}): {todays_action}")
                else:
                    print("todaysAction: MISSING")
                
                print(f"Plan A Expected Participant: {plan_a.get('expectedParticipant')}")
                print(f"Plan B Expected Participant: {plan_b.get('expectedParticipant')}")
                print()
                
            except Exception as e:
                print(f"Error parsing {ticker}: {e}\n")

    except Exception as e:
        print(f"Fatal error: {e}")

if __name__ == "__main__":
    main()
