
import os
# Disable Infisical BEFORE any imports
os.environ["DISABLE_INFISICAL"] = "1"

import json
from datetime import date
from unittest.mock import MagicMock, patch
import modules.ai.ai_services as ai

def test_immutability_violation():
    ticker = "AAPL"
    previous_card = ai.DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", ticker)
    today = date(2024, 1, 1)
    
    # First Mock Response
    mock_resp1 = {
        "marketNote": "Note 1",
        "confidence": "High",
        "screener_briefing": "...",
        "basicContext": {"tickerDate": "...", "sector": "...", "companyDescription": "...", "priceTrend": "...", "recentCatalyst": "..."},
        "technicalStructure": {"majorSupport": "...", "majorResistance": "...", "pattern": "...", "volumeMomentum": "..."},
        "fundamentalContext": {"analystSentiment": "...", "insiderActivity": "...", "peerPerformance": "..."},
        "behavioralSentiment": {"buyerVsSeller": "...", "emotionalTone": "...", "newsReaction": "..."},
        "openingTradePlan": {"planName": "...", "knownParticipant": "...", "expectedParticipant": "...", "trigger": "...", "invalidation": "..."},
        "alternativePlan": {"planName": "...", "scenario": "...", "knownParticipant": "...", "expectedParticipant": "...", "trigger": "...", "invalidation": "..."},
        "todaysAction": "Action ONE"
    }
    
    # Second Mock Response (Same Date)
    mock_resp2 = mock_resp1.copy()
    mock_resp2["todaysAction"] = "Action TWO (The Overwrite)"
    
    # Mock both Gemini API and Impact Engine context
    with patch('modules.ai.ai_services.call_gemini_api') as mock_api, \
         patch('modules.ai.ai_services.get_or_compute_context') as mock_context:
        
        mock_context.return_value = {"meta": {"data_points": 0}}
        
        # Run 1
        mock_api.return_value = json.dumps(mock_resp1)
        card1 = ai.update_company_card(ticker, previous_card, "2023-12-31", "", today, "model", "Mock News")
        
        # Run 2 (Same Date)
        mock_api.return_value = json.dumps(mock_resp2)
        card2 = ai.update_company_card(ticker, card1, "2024-01-01", "", today, "model", "Mock News")
        
        card2_dict = json.loads(card2)
        log = card2_dict['technicalStructure']['keyActionLog']
        
        print(f"Log entries for {today}:")
        for entry in log:
            if entry['date'] == today.isoformat():
                print(f" - {entry['action']}")
        
        # In current code, it overwrites if date exists
        if len([e for e in log if e['date'] == today.isoformat()]) == 1 and log[-1]['action'] == "Action TWO (The Overwrite)":
            print("\n❌ IMMUTABILITY VIOLATION CONFIRMED: Previous action for today was overwritten.")
        else:
            print("\n✅ IMMUTABILITY PRESERVED: (Either appended or rejected rewrite)")

if __name__ == "__main__":
    test_immutability_violation()
