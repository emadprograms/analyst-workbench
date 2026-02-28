import json
from modules.data.db_utils import get_table_data
from modules.ai.quality_validators import validate_company_card

def get_latest_card(ticker):
    df = get_table_data('aw_company_cards')
    df_filtered = df[df['ticker'] == ticker]
    if not df_filtered.empty:
        # Get the most recent one (already sorted by date desc in get_table_data)
        return json.loads(df_filtered.iloc[0]['company_card_json'])
    return None

def main():
    for ticker in ['MSFT', 'ORCL']:
        card = get_latest_card(ticker)
        if card:
            qr = validate_company_card(card, ticker=ticker)
            print(f"--- {ticker} Warnings ---")
            for issue in qr.issues:
                if issue.severity == 'warning':
                    print(f"[{issue.rule}] {issue.field}: {issue.message}")
            print()
        else:
            print(f"No card found for {ticker}")

if __name__ == "__main__":
    main()
