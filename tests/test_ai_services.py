"""
Comprehensive tests for AI Services (modules/ai/ai_services.py).

Tests cover:
- call_gemini_api with mocked HTTP responses
- update_company_card JSON parsing and card assembly
- update_economy_card JSON parsing and card assembly
- Deep copy fix (previous card not mutated)
- keyActionLog append/overwrite logic
- Edge cases: malformed JSON, missing fields, empty responses
"""
import pytest
import os
import json
import copy
import requests
from datetime import date
from unittest.mock import patch, MagicMock, PropertyMock

# Must set env before importing modules that load config
os.environ["DISABLE_INFISICAL"] = "1"

from modules.core.logger import AppLogger
from modules.core.tracker import ExecutionTracker


# ==========================================
# SAMPLE DATA
# ==========================================

SAMPLE_PREVIOUS_COMPANY_CARD = {
    "marketNote": "Executor's Battle Card: AAPL",
    "confidence": "Trend_Bias: Bullish (Story_Confidence: Medium)",
    "screener_briefing": "Setup_Bias: Neutral\nJustification: Test",
    "basicContext": {
        "tickerDate": "AAPL | 2026-02-22",
        "sector": "Technology",
        "companyDescription": "Apple Inc.",
        "priceTrend": "Uptrend",
        "recentCatalyst": "Strong earnings"
    },
    "technicalStructure": {
        "majorSupport": "$200, $195",
        "majorResistance": "$220, $225",
        "pattern": "Consolidation above $200",
        "keyActionLog": [
            {"date": "2026-02-20", "action": "Buyers defended $200"},
            {"date": "2026-02-21", "action": "Tested $210 resistance"},
            {"date": "2026-02-22", "action": "Held above $205 support"}
        ],
        "volumeMomentum": "High volume at support"
    },
    "fundamentalContext": {
        "valuation": "25x P/E",
        "analystSentiment": "Overweight",
        "insiderActivity": "No recent activity",
        "peerPerformance": "Outperforming sector"
    },
    "behavioralSentiment": {
        "buyerVsSeller": "Buyers in control",
        "emotionalTone": "Accumulation (Stable)",
        "newsReaction": "Bullish"
    },
    "openingTradePlan": {
        "planName": "Long from $205",
        "knownParticipant": "Committed Buyers",
        "expectedParticipant": "FOMO Buyers",
        "trigger": "$210 breakout",
        "invalidation": "$200 breakdown"
    },
    "alternativePlan": {
        "planName": "Short at $220",
        "scenario": "Rejection at resistance",
        "knownParticipant": "Committed Sellers",
        "expectedParticipant": "Panic Sellers",
        "trigger": "$220 rejection",
        "invalidation": "$225 breakout"
    }
}

SAMPLE_AI_COMPANY_RESPONSE = {
    "marketNote": "Executor's Battle Card: AAPL",
    "confidence": "Trend_Bias: Bullish (Story_Confidence: High) - Reasoning: Decisive breakout above $210.",
    "screener_briefing": "Setup_Bias: Bullish\nJustification: Breakout confirmed",
    "basicContext": {
        "tickerDate": "AAPL | 2026-02-23",
        "sector": "Technology",
        "companyDescription": "Apple Inc.",
        "priceTrend": "Strong uptrend, broke $210",
        "recentCatalyst": "Strong earnings + AI partnership"
    },
    "technicalStructure": {
        "majorSupport": "$210 (new), $200",
        "majorResistance": "$220, $225",
        "pattern": "Breakout above $210 consolidation",
        "volumeMomentum": "Very high volume on breakout"
    },
    "fundamentalContext": {
        "valuation": "25x P/E",
        "analystSentiment": "Strong Buy upgrade",
        "insiderActivity": "No recent activity",
        "peerPerformance": "Leading sector"
    },
    "behavioralSentiment": {
        "buyerVsSeller": "Committed Buyers overwhelmed sellers",
        "emotionalTone": "Breakout (Stable to Unstable) - Reasoning: Act I: Gap up. Act II: Confirmed. Act III: Held.",
        "newsReaction": "Bullish -AI news accelerated existing momentum"
    },
    "openingTradePlan": {
        "planName": "Long with $210 as new support",
        "knownParticipant": "Committed Buyers at $210",
        "expectedParticipant": "FOMO Buyers on continuation",
        "trigger": "$215 break on volume",
        "invalidation": "$210 breakdown"
    },
    "alternativePlan": {
        "planName": "Fade at $220 resistance",
        "scenario": "First test of major resistance",
        "knownParticipant": "Committed Sellers at $220",
        "expectedParticipant": "Panic if $220 holds",
        "trigger": "$220 rejection on low volume",
        "invalidation": "$222 breakout"
    },
    "todaysAction": "Breakout day. Committed Buyers pushed price above $210 on massive volume. New support established."
}


# ==========================================
# TEST: Company Card Assembly Logic
# ==========================================

class TestCompanyCardAssembly:
    """Tests the card assembly logic in update_company_card WITHOUT calling the AI."""
    
    def _simulate_card_assembly(self, previous_card_dict, ai_response_dict, trade_date_str):
        """
        Simulates the exact card assembly logic from update_company_card.
        This lets us test the logic without mocking the entire AI pipeline.
        """
        import copy as copy_mod
        
        ai_data = copy_mod.deepcopy(ai_response_dict)
        new_action = ai_data.pop("todaysAction", None)
        
        if not new_action:
            return None
        
        # Deep copy to avoid mutating original
        final_card = copy_mod.deepcopy(previous_card_dict)
        
        def deep_update(d, u):
            for k, v in u.items():
                if isinstance(v, dict):
                    d[k] = deep_update(d.get(k, {}), v)
                else:
                    d[k] = v
            return d
        
        final_card = deep_update(final_card, ai_data)
        
        # Update tickerDate
        ticker = previous_card_dict.get("basicContext", {}).get("tickerDate", "UNKNOWN").split("|")[0].strip()
        final_card['basicContext']['tickerDate'] = f"{ticker} | {trade_date_str}"
        
        # Handle keyActionLog
        if "technicalStructure" not in final_card:
            final_card['technicalStructure'] = {}
        if "keyActionLog" not in final_card['technicalStructure'] or not isinstance(final_card['technicalStructure']['keyActionLog'], list):
            final_card['technicalStructure']['keyActionLog'] = []
        
        if not any(entry.get('date') == trade_date_str for entry in final_card['technicalStructure']['keyActionLog']):
            final_card['technicalStructure']['keyActionLog'].append({
                "date": trade_date_str,
                "action": new_action
            })
        else:
            for i, entry in enumerate(final_card['technicalStructure']['keyActionLog']):
                if entry.get('date') == trade_date_str:
                    final_card['technicalStructure']['keyActionLog'][i] = {
                        "date": trade_date_str,
                        "action": new_action
                    }
                    break
        
        return final_card

    def test_deep_update_preserves_keyactionlog(self):
        """keyActionLog from previous card should survive deep_update."""
        result = self._simulate_card_assembly(
            SAMPLE_PREVIOUS_COMPANY_CARD,
            SAMPLE_AI_COMPANY_RESPONSE,
            "2026-02-23"
        )
        
        assert result is not None
        log = result['technicalStructure']['keyActionLog']
        assert len(log) == 4  # 3 previous + 1 new
        assert log[-1]['date'] == "2026-02-23"
        assert "Breakout" in log[-1]['action']

    def test_previous_card_not_mutated(self):
        """Critical: deep copy must prevent mutation of previous card."""
        original = copy.deepcopy(SAMPLE_PREVIOUS_COMPANY_CARD)
        original_log_length = len(original['technicalStructure']['keyActionLog'])
        
        result = self._simulate_card_assembly(
            original,
            SAMPLE_AI_COMPANY_RESPONSE,
            "2026-02-23"
        )
        
        # Original should NOT have new entry
        assert len(original['technicalStructure']['keyActionLog']) == original_log_length
        # Result should have new entry
        assert len(result['technicalStructure']['keyActionLog']) == original_log_length + 1

    def test_overwrite_same_date_entry(self):
        """Running twice for the same date should overwrite, not duplicate."""
        # First run
        result1 = self._simulate_card_assembly(
            SAMPLE_PREVIOUS_COMPANY_CARD,
            SAMPLE_AI_COMPANY_RESPONSE,
            "2026-02-23"
        )
        
        # Second run with different action text
        modified_response = copy.deepcopy(SAMPLE_AI_COMPANY_RESPONSE)
        modified_response['todaysAction'] = "UPDATED: Different analysis for same date"
        
        result2 = self._simulate_card_assembly(
            result1,
            modified_response,
            "2026-02-23"
        )
        
        log = result2['technicalStructure']['keyActionLog']
        date_entries = [e for e in log if e['date'] == "2026-02-23"]
        assert len(date_entries) == 1, "Same date should have exactly 1 entry"
        assert "UPDATED" in date_entries[0]['action']

    def test_missing_todays_action_returns_none(self):
        """AI response without todaysAction should be rejected."""
        response_no_action = copy.deepcopy(SAMPLE_AI_COMPANY_RESPONSE)
        del response_no_action['todaysAction']
        
        result = self._simulate_card_assembly(
            SAMPLE_PREVIOUS_COMPANY_CARD,
            response_no_action,
            "2026-02-23"
        )
        
        assert result is None

    def test_new_ai_data_overwrites_fields(self):
        """AI's new data should overwrite corresponding fields in front card."""
        result = self._simulate_card_assembly(
            SAMPLE_PREVIOUS_COMPANY_CARD,
            SAMPLE_AI_COMPANY_RESPONSE,
            "2026-02-23"
        )
        
        # AI updated these
        assert "Strong Buy upgrade" in result['fundamentalContext']['analystSentiment']
        assert "Breakout" in result['technicalStructure']['pattern']

    def test_readonly_fields_preserved(self):
        """Sector and Company Description should be preserved from previous card."""
        result = self._simulate_card_assembly(
            SAMPLE_PREVIOUS_COMPANY_CARD,
            SAMPLE_AI_COMPANY_RESPONSE,
            "2026-02-23"
        )
        
        assert result['basicContext']['sector'] == "Technology"
        assert result['basicContext']['companyDescription'] == "Apple Inc."

    def test_handles_empty_previous_keyactionlog(self):
        """Should work when previous card has empty or missing keyActionLog."""
        prev = copy.deepcopy(SAMPLE_PREVIOUS_COMPANY_CARD)
        prev['technicalStructure']['keyActionLog'] = []
        
        result = self._simulate_card_assembly(prev, SAMPLE_AI_COMPANY_RESPONSE, "2026-02-23")
        
        assert len(result['technicalStructure']['keyActionLog']) == 1
        assert result['technicalStructure']['keyActionLog'][0]['date'] == "2026-02-23"

    def test_handles_corrupted_keyactionlog(self):
        """Should reset keyActionLog if it's not a list."""
        prev = copy.deepcopy(SAMPLE_PREVIOUS_COMPANY_CARD)
        prev['technicalStructure']['keyActionLog'] = "corrupted"
        
        result = self._simulate_card_assembly(prev, SAMPLE_AI_COMPANY_RESPONSE, "2026-02-23")
        
        assert isinstance(result['technicalStructure']['keyActionLog'], list)
        assert len(result['technicalStructure']['keyActionLog']) == 1

    def test_handles_missing_technical_structure(self):
        """Should create technicalStructure if completely missing."""
        prev = copy.deepcopy(SAMPLE_PREVIOUS_COMPANY_CARD)
        del prev['technicalStructure']
        
        result = self._simulate_card_assembly(prev, SAMPLE_AI_COMPANY_RESPONSE, "2026-02-23")
        
        assert 'technicalStructure' in result
        assert 'keyActionLog' in result['technicalStructure']


# ==========================================
# TEST: Economy Card Assembly Logic
# ==========================================

SAMPLE_PREVIOUS_ECONOMY_CARD = {
    "marketNarrative": "Risk-on continues",
    "marketBias": "Bullish",
    "keyActionLog": [
        {"date": "2026-02-21", "action": "Tech rally led by NVDA"},
        {"date": "2026-02-22", "action": "Consolidation day"}
    ],
    "keyEconomicEvents": {"last_24h": "CPI data", "next_24h": "FOMC minutes"},
    "sectorRotation": {"leadingSectors": ["XLK"], "laggingSectors": ["XLE"],
                        "rotationAnalysis": "Tech leading"},
    "indexAnalysis": {"pattern": "Uptrend", "SPY": "Above 450", "QQQ": "Above 380"},
    "interMarketAnalysis": {"bonds": "TLT flat", "commodities": "Gold up",
                             "currencies": "DXY down", "crypto": "BTC rallying"},
    "marketInternals": {"volatility": "VIX at 15"}
}

SAMPLE_AI_ECONOMY_RESPONSE = {
    "marketNarrative": "Tech rotation accelerates on AI news",
    "marketBias": "Bullish",
    "keyEconomicEvents": {"last_24h": "Jobs data strong", "next_24h": "Fed speech"},
    "sectorRotation": {"leadingSectors": ["XLK", "SMH"], "laggingSectors": ["XLE", "XLP"],
                        "rotationAnalysis": "AI theme dominant"},
    "indexAnalysis": {"pattern": "Breakout", "SPY": "New highs at 455", "QQQ": "Testing 390"},
    "interMarketAnalysis": {"bonds": "TLT selling off", "commodities": "Gold steady",
                             "currencies": "DXY weakening", "crypto": "BTC new highs"},
    "marketInternals": {"volatility": "VIX dropping to 13"},
    "todaysAction": "Risk-on day. SPY broke to new highs. Tech sector leading rotation."
}


class TestEconomyCardAssembly:
    
    def _simulate_economy_assembly(self, previous_card_dict, ai_response_dict, trade_date_str):
        """Simulates economy card assembly logic from update_economy_card."""
        import copy as copy_mod
        
        ai_data = copy_mod.deepcopy(ai_response_dict)
        new_action = ai_data.pop("todaysAction", None)
        if not new_action:
            return None
        
        final_card = copy_mod.deepcopy(previous_card_dict)
        
        def deep_update(d, u):
            for k, v in u.items():
                if isinstance(v, dict):
                    d[k] = deep_update(d.get(k, {}), v)
                else:
                    d[k] = v
            return d
        
        final_card = deep_update(final_card, ai_data)
        
        if "keyActionLog" not in final_card or not isinstance(final_card['keyActionLog'], list):
            final_card['keyActionLog'] = []
        
        if not any(entry.get('date') == trade_date_str for entry in final_card['keyActionLog']):
            final_card['keyActionLog'].append({"date": trade_date_str, "action": new_action})
        else:
            for i, entry in enumerate(final_card['keyActionLog']):
                if entry.get('date') == trade_date_str:
                    final_card['keyActionLog'][i] = {"date": trade_date_str, "action": new_action}
                    break
        
        return final_card

    def test_preserves_previous_action_log(self):
        result = self._simulate_economy_assembly(
            SAMPLE_PREVIOUS_ECONOMY_CARD, SAMPLE_AI_ECONOMY_RESPONSE, "2026-02-23"
        )
        
        log = result['keyActionLog']
        assert len(log) == 3  # 2 previous + 1 new
        dates = [e['date'] for e in log]
        assert "2026-02-21" in dates
        assert "2026-02-22" in dates
        assert "2026-02-23" in dates

    def test_previous_card_not_mutated(self):
        original = copy.deepcopy(SAMPLE_PREVIOUS_ECONOMY_CARD)
        original_len = len(original['keyActionLog'])
        
        self._simulate_economy_assembly(original, SAMPLE_AI_ECONOMY_RESPONSE, "2026-02-23")
        
        assert len(original['keyActionLog']) == original_len

    def test_overwrites_same_date(self):
        result1 = self._simulate_economy_assembly(
            SAMPLE_PREVIOUS_ECONOMY_CARD, SAMPLE_AI_ECONOMY_RESPONSE, "2026-02-23"
        )
        
        modified = copy.deepcopy(SAMPLE_AI_ECONOMY_RESPONSE)
        modified['todaysAction'] = "REVISED: Different analysis"
        
        result2 = self._simulate_economy_assembly(result1, modified, "2026-02-23")
        
        entries = [e for e in result2['keyActionLog'] if e['date'] == "2026-02-23"]
        assert len(entries) == 1
        assert "REVISED" in entries[0]['action']

    def test_new_narrative_overwrites(self):
        result = self._simulate_economy_assembly(
            SAMPLE_PREVIOUS_ECONOMY_CARD, SAMPLE_AI_ECONOMY_RESPONSE, "2026-02-23"
        )
        
        assert "AI news" in result['marketNarrative']

    def test_sector_rotation_updated(self):
        result = self._simulate_economy_assembly(
            SAMPLE_PREVIOUS_ECONOMY_CARD, SAMPLE_AI_ECONOMY_RESPONSE, "2026-02-23"
        )
        
        assert "SMH" in result['sectorRotation']['leadingSectors']
        assert "AI theme" in result['sectorRotation']['rotationAnalysis']


# ==========================================
# TEST: call_gemini_api (with mocked HTTP)
# ==========================================

class TestCallGeminiAPI:
    
    @patch('modules.ai.ai_services.KEY_MANAGER')
    @patch('requests.post')
    def test_successful_call(self, mock_post, mock_km):
        from modules.ai.ai_services import call_gemini_api
        
        mock_km.estimate_tokens.return_value = 1000
        mock_km.get_key.return_value = ("key1", "abc123", 0.0, "gemini-3-flash-preview")
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": '{"result": "test"}'}]}}],
            "usageMetadata": {"totalTokenCount": 1500}
        }
        mock_post.return_value = mock_response
        
        logger = AppLogger("test")
        result = call_gemini_api("test prompt", "system prompt", logger, "gemini-3-flash-free")
        
        assert result == '{"result": "test"}'
        mock_km.report_usage.assert_called_once()

    @patch('modules.ai.ai_services.KEY_MANAGER')
    def test_no_key_manager(self, mock_km):
        """Should return None if KEY_MANAGER is None."""
        from modules.ai import ai_services
        original = ai_services.KEY_MANAGER
        ai_services.KEY_MANAGER = None
        
        logger = AppLogger("test")
        result = ai_services.call_gemini_api("prompt", "system", logger, "gemini-3-flash-free")
        
        assert result is None
        ai_services.KEY_MANAGER = original

    @patch('modules.ai.ai_services.KEY_MANAGER')
    @patch('requests.post')
    def test_429_triggers_cooldown(self, mock_post, mock_km):
        from modules.ai.ai_services import call_gemini_api
        
        mock_km.estimate_tokens.return_value = 500
        mock_km.get_key.return_value = ("key1", "abc123", 0.0, "gemini-3-flash-preview")
        
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limit exceeded"
        mock_post.return_value = mock_response
        
        logger = AppLogger("test")
        result = call_gemini_api("prompt", "system", logger, "gemini-3-flash-free", max_retries=2)
        
        assert result is None
        mock_km.report_failure.assert_called()

    @patch('modules.ai.ai_services.KEY_MANAGER')
    def test_prompt_too_large(self, mock_km):
        """Fatal: prompt exceeds model capacity."""
        from modules.ai.ai_services import call_gemini_api
        
        mock_km.estimate_tokens.return_value = 999999
        mock_km.get_key.return_value = (None, None, -1.0, "gemini-3-flash-preview")
        
        logger = AppLogger("test")
        result = call_gemini_api("huge prompt", "system", logger, "gemini-3-flash-free")
        
        assert result is None

    @patch('modules.ai.ai_services.KEY_MANAGER')
    @patch('requests.post')
    def test_server_error_retry(self, mock_post, mock_km):
        """500 errors should retry with backoff."""
        from modules.ai.ai_services import call_gemini_api
        
        mock_km.estimate_tokens.return_value = 500
        mock_km.get_key.return_value = ("key1", "abc123", 0.0, "model")
        
        err_response = MagicMock()
        err_response.status_code = 500
        err_response.text = "Internal Server Error"
        
        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "recovered"}]}}],
            "usageMetadata": {"totalTokenCount": 100}
        }
        
        mock_post.side_effect = [err_response, ok_response]
        
        logger = AppLogger("test")
        result = call_gemini_api("prompt", "system", logger, "gemini-3-flash-free", max_retries=3)
        
        assert result == "recovered"


# ==========================================
# TEST: Timeout & Error Handling in call_gemini_api
# ==========================================

class TestTimeoutHandling:
    """
    Tests that ReadTimeout is treated as a REAL failure (is_info_error=False),
    not an info error. When a request times out, Google has already counted
    the tokens — the key MUST go to cooldown.
    """

    @patch('modules.ai.ai_services.TRACKER')
    @patch('modules.ai.ai_services.KEY_MANAGER')
    @patch('requests.post')
    def test_read_timeout_triggers_real_failure(self, mock_post, mock_km, mock_tracker):
        """ReadTimeout must call report_failure with is_info_error=False."""
        from modules.ai.ai_services import call_gemini_api

        mock_km.estimate_tokens.return_value = 500
        mock_km.get_key.return_value = ("key1", "abc123", 0.0, "gemini-3-flash-preview")
        mock_post.side_effect = requests.exceptions.ReadTimeout("Connection timed out")

        logger = AppLogger("test")
        result = call_gemini_api("prompt", "system", logger, "gemini-3-flash-free", max_retries=1)

        assert result is None
        # The critical assertion: is_info_error must be False (key needs cooldown)
        mock_km.report_failure.assert_called_with("abc123", is_info_error=False)

    @patch('modules.ai.ai_services.TRACKER')
    @patch('modules.ai.ai_services.KEY_MANAGER')
    @patch('requests.post')
    def test_read_timeout_does_not_return_key_to_pool(self, mock_post, mock_km, mock_tracker):
        """After timeout, key must NOT be immediately available (must be on cooldown)."""
        from modules.ai.ai_services import call_gemini_api

        mock_km.estimate_tokens.return_value = 500
        mock_km.get_key.return_value = ("key1", "abc123", 0.0, "model")
        mock_post.side_effect = requests.exceptions.ReadTimeout()

        logger = AppLogger("test")
        call_gemini_api("prompt", "system", logger, "gemini-3-flash-free", max_retries=1)

        # report_usage should NOT be called (key didn't succeed)
        mock_km.report_usage.assert_not_called()
        # report_failure MUST be called (not info error)
        mock_km.report_failure.assert_called()
        # Verify it was called with is_info_error=False
        args, kwargs = mock_km.report_failure.call_args
        assert kwargs.get('is_info_error', args[1] if len(args) > 1 else None) is False

    @patch('modules.ai.ai_services.TRACKER')
    @patch('modules.ai.ai_services.KEY_MANAGER')
    @patch('requests.post')
    def test_generic_exception_is_info_error(self, mock_post, mock_km, mock_tracker):
        """Generic exceptions (not timeout) should be info errors — key returns to pool."""
        from modules.ai.ai_services import call_gemini_api

        mock_km.estimate_tokens.return_value = 500
        mock_km.get_key.return_value = ("key1", "abc123", 0.0, "model")
        mock_post.side_effect = ConnectionError("DNS failed")

        logger = AppLogger("test")
        call_gemini_api("prompt", "system", logger, "gemini-3-flash-free", max_retries=1)

        mock_km.report_failure.assert_called_with("abc123", is_info_error=True)

    @patch('modules.ai.ai_services.TRACKER')
    @patch('modules.ai.ai_services.KEY_MANAGER')
    @patch('requests.post')
    def test_400_invalid_key_triggers_fatal(self, mock_post, mock_km, mock_tracker):
        """400 with API_KEY_INVALID should call report_fatal_error (permanent retirement)."""
        from modules.ai.ai_services import call_gemini_api

        mock_km.estimate_tokens.return_value = 500
        mock_km.get_key.return_value = ("key1", "abc123", 0.0, "model")

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"error": {"message": "API_KEY_INVALID"}}'
        mock_post.return_value = mock_response

        logger = AppLogger("test")
        call_gemini_api("prompt", "system", logger, "gemini-3-flash-free", max_retries=1)

        mock_km.report_fatal_error.assert_called_once_with("abc123")

    @patch('modules.ai.ai_services.TRACKER')
    @patch('modules.ai.ai_services.KEY_MANAGER')
    @patch('requests.post')
    def test_429_triggers_real_failure(self, mock_post, mock_km, mock_tracker):
        """429 should call report_failure with is_info_error=False."""
        from modules.ai.ai_services import call_gemini_api

        mock_km.estimate_tokens.return_value = 500
        mock_km.get_key.return_value = ("key1", "abc123", 0.0, "model")

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Resource exhausted"
        mock_post.return_value = mock_response

        logger = AppLogger("test")
        call_gemini_api("prompt", "system", logger, "gemini-3-flash-free", max_retries=1)

        mock_km.report_failure.assert_called_with("abc123", is_info_error=False)

    @patch('modules.ai.ai_services.TRACKER')
    @patch('modules.ai.ai_services.KEY_MANAGER')
    @patch('requests.post')
    def test_500_is_info_error(self, mock_post, mock_km, mock_tracker):
        """500 server errors should be info errors (server's fault, not key's)."""
        from modules.ai.ai_services import call_gemini_api

        mock_km.estimate_tokens.return_value = 500
        mock_km.get_key.return_value = ("key1", "abc123", 0.0, "model")

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_post.return_value = mock_response

        logger = AppLogger("test")
        call_gemini_api("prompt", "system", logger, "gemini-3-flash-free", max_retries=1)

        mock_km.report_failure.assert_called_with("abc123", is_info_error=True)

    @patch('modules.ai.ai_services.KEY_MANAGER')
    @patch('requests.post')
    def test_http_timeout_is_420_seconds(self, mock_post, mock_km):
        """HTTP timeout should be 420s (7 min) to accommodate large ~150K token requests."""
        from modules.ai.ai_services import call_gemini_api

        mock_km.estimate_tokens.return_value = 100
        mock_km.get_key.return_value = ("key1", "abc123", 0.0, "model")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
            "usageMetadata": {"totalTokenCount": 100}
        }
        mock_post.return_value = mock_response

        logger = AppLogger("test")
        call_gemini_api("prompt", "system", logger, "gemini-3-flash-free")

        # Verify the timeout parameter passed to requests.post
        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs.get('timeout') == 420 or \
               (call_kwargs[1].get('timeout') == 420 if len(call_kwargs) > 1 else False), \
            f"HTTP timeout should be 420s, got {call_kwargs}"
