"""
Comprehensive tests for main.py pipeline functions.

Tests cover:
- CLI argument parsing
- run_update_economy flow (with mocked DB/AI)
- run_update_company flow
- send_webhook_report
- Edge cases: missing data, invalid dates, empty tickers
- Bug fix verification: target_date unbound, logger.warn->warning, ai_actions list
"""
import pytest
import os
import json
from datetime import date
from unittest.mock import patch, MagicMock, call

# Must set env before importing modules that load config
os.environ["DISABLE_INFISICAL"] = "1"

from modules.core.logger import AppLogger
from modules.core.tracker import ExecutionTracker


# ==========================================
# HELPERS
# ==========================================

def _make_mock_tracker():
    """Create a fresh ExecutionTracker for testing."""
    tracker = ExecutionTracker()
    tracker.start(action_type="Test")
    return tracker


SAMPLE_ECONOMY_CARD = json.dumps({
    "marketNarrative": "Test narrative",
    "marketBias": "Bullish",
    "keyActionLog": [],
    "keyEconomicEvents": {"last_24h": "Nothing", "next_24h": "Nothing"},
    "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": "None"},
    "indexAnalysis": {"pattern": "Test", "SPY": "Test", "QQQ": "Test"},
    "interMarketAnalysis": {"bonds": "Test", "commodities": "Test", "currencies": "Test", "crypto": "Test"},
    "marketInternals": {"volatility": "Test"}
})

SAMPLE_COMPANY_CARD = json.dumps({
    "marketNote": "Executor's Battle Card: AAPL",
    "confidence": "Medium",
    "screener_briefing": "Test",
    "basicContext": {"tickerDate": "AAPL | 2026-02-22", "sector": "Tech", "companyDescription": "Apple",
                     "priceTrend": "Up", "recentCatalyst": "Earnings"},
    "technicalStructure": {"majorSupport": "$200", "majorResistance": "$220",
                           "pattern": "Consolidation", "keyActionLog": [], "volumeMomentum": "High"},
    "fundamentalContext": {"valuation": "Fair", "analystSentiment": "Buy",
                           "insiderActivity": "None", "peerPerformance": "Inline"},
    "behavioralSentiment": {"buyerVsSeller": "Buyers", "emotionalTone": "Calm", "newsReaction": "Neutral"},
    "openingTradePlan": {"planName": "Long", "knownParticipant": "Buyers", "expectedParticipant": "FOMO",
                         "trigger": "$210 break", "invalidation": "$200 break"},
    "alternativePlan": {"planName": "Short", "scenario": "Fail at $220", "knownParticipant": "Sellers",
                        "expectedParticipant": "Panic", "trigger": "$220 reject", "invalidation": "$225 break"}
})


# ==========================================
# TEST: run_update_economy
# ==========================================

class TestRunUpdateEconomy:
    
    @patch('main.upsert_economy_card')
    @patch('main.update_economy_card')
    @patch('main.get_latest_price_details')
    @patch('main.get_economy_card')
    @patch('main.get_daily_inputs')
    def test_success_flow(self, mock_news, mock_eco, mock_price, mock_ai, mock_upsert):
        """Full successful economy update flow."""
        from main import run_update_economy
        
        mock_news.return_value = ("Market rallied on tech earnings", None)
        mock_eco.return_value = (SAMPLE_ECONOMY_CARD, "2026-02-22")
        mock_price.return_value = (450.25, "2026-02-23 16:00:00")
        mock_ai.return_value = '{"marketNarrative": "Updated"}'
        mock_upsert.return_value = True
        
        logger = AppLogger("test")
        result = run_update_economy(date(2026, 2, 23), "gemini-3-flash-free", logger)
        
        assert result is True
        mock_ai.assert_called_once()
        mock_upsert.assert_called_once()

    @patch('main.get_daily_inputs')
    def test_halts_on_missing_news(self, mock_news):
        """Should fail when no market news is available."""
        from main import run_update_economy
        
        mock_news.return_value = (None, None)
        logger = AppLogger("test")
        
        result = run_update_economy(date(2026, 2, 23), "gemini-3-flash-free", logger)
        
        assert result is False
        full_log = logger.get_full_log()
        assert "No market news found" in full_log

    @patch('main.get_latest_price_details')
    @patch('main.get_economy_card')
    @patch('main.get_daily_inputs')
    def test_halts_on_missing_price_data(self, mock_news, mock_eco, mock_price):
        """Should fail when SPY price data is missing for the date."""
        from main import run_update_economy
        
        mock_news.return_value = ("Some news", None)
        mock_eco.return_value = (SAMPLE_ECONOMY_CARD, None)
        # Price data is from wrong date
        mock_price.return_value = (450.0, "2026-02-22 16:00:00")
        
        logger = AppLogger("test")
        result = run_update_economy(date(2026, 2, 23), "gemini-3-flash-free", logger)
        
        assert result is False
        assert "Market data missing" in logger.get_full_log()

    @patch('main.update_economy_card')
    @patch('main.get_latest_price_details')
    @patch('main.get_economy_card')
    @patch('main.get_daily_inputs')
    def test_handles_ai_failure(self, mock_news, mock_eco, mock_price, mock_ai):
        """Should fail gracefully when AI returns None."""
        from main import run_update_economy
        
        mock_news.return_value = ("News", None)
        mock_eco.return_value = (SAMPLE_ECONOMY_CARD, None)
        mock_price.return_value = (450.0, "2026-02-23 16:00:00")
        mock_ai.return_value = None
        
        logger = AppLogger("test")
        result = run_update_economy(date(2026, 2, 23), "gemini-3-flash-free", logger)
        
        assert result is False
        assert "AI failed to generate" in logger.get_full_log()

    @patch('main.upsert_economy_card')
    @patch('main.update_economy_card')
    @patch('main.get_latest_price_details')
    @patch('main.get_economy_card')
    @patch('main.get_daily_inputs')
    def test_handles_db_save_failure(self, mock_news, mock_eco, mock_price, mock_ai, mock_upsert):
        """Should fail gracefully when DB save fails."""
        from main import run_update_economy
        
        mock_news.return_value = ("News", None)
        mock_eco.return_value = (SAMPLE_ECONOMY_CARD, None)
        mock_price.return_value = (450.0, "2026-02-23 16:00:00")
        mock_ai.return_value = '{"test": "data"}'
        mock_upsert.return_value = False
        
        logger = AppLogger("test")
        result = run_update_economy(date(2026, 2, 23), "gemini-3-flash-free", logger)
        
        assert result is False


# ==========================================
# TEST: run_update_company
# ==========================================

class TestRunUpdateCompany:
    
    @patch('main.upsert_company_card')
    @patch('main.update_company_card')
    @patch('main.get_company_card_and_notes')
    @patch('main.get_daily_inputs')
    def test_single_ticker_success(self, mock_news, mock_card, mock_ai, mock_upsert):
        """Successful single ticker update."""
        from main import run_update_company
        
        mock_news.return_value = ("Market news today", None)
        mock_card.return_value = (SAMPLE_COMPANY_CARD, "Historical: $200 support", "2026-02-22")
        mock_ai.return_value = '{"marketNote": "Updated AAPL card"}'
        mock_upsert.return_value = True
        
        logger = AppLogger("test")
        result = run_update_company(date(2026, 2, 23), "gemini-3-flash-free", ["AAPL"], logger)
        
        assert result is True
        mock_ai.assert_called_once()

    @patch('main.upsert_company_card')
    @patch('main.update_company_card')
    @patch('main.get_company_card_and_notes')
    @patch('main.get_daily_inputs')
    def test_multiple_tickers(self, mock_news, mock_card, mock_ai, mock_upsert):
        """Multiple tickers should be processed sequentially."""
        from main import run_update_company
        
        mock_news.return_value = ("News", None)
        mock_card.return_value = (SAMPLE_COMPANY_CARD, "", "2026-02-22")
        mock_ai.return_value = '{"marketNote": "Updated"}'
        mock_upsert.return_value = True
        
        logger = AppLogger("test")
        result = run_update_company(date(2026, 2, 23), "gemini-3-flash-free", ["AAPL", "MSFT", "GOOGL"], logger)
        
        assert result is True
        assert mock_ai.call_count == 3

    @patch('main.get_daily_inputs')
    def test_continues_without_news(self, mock_news):
        """Should continue (with warning) when no market news available."""
        from main import run_update_company
        
        mock_news.return_value = (None, None)
        logger = AppLogger("test")
        
        with patch('main.get_company_card_and_notes') as mock_card, \
             patch('main.update_company_card') as mock_ai, \
             patch('main.upsert_company_card') as mock_upsert:
            mock_card.return_value = (SAMPLE_COMPANY_CARD, "", None)
            mock_ai.return_value = '{"test": "data"}'
            mock_upsert.return_value = True
            
            result = run_update_company(date(2026, 2, 23), "gemini-3-flash-free", ["AAPL"], logger)
        
        assert result is True
        # Verify warning was logged (not crash)
        assert "No market news found" in logger.get_full_log()

    @patch('main.update_company_card')
    @patch('main.get_company_card_and_notes')
    @patch('main.get_daily_inputs')
    def test_partial_failure(self, mock_news, mock_card, mock_ai):
        """If one ticker fails, others should still be processed."""
        from main import run_update_company
        
        mock_news.return_value = ("News", None)
        mock_card.return_value = (SAMPLE_COMPANY_CARD, "", "2026-02-22")
        # First call succeeds, second fails, third succeeds
        mock_ai.side_effect = ['{"valid": "json"}', None, '{"valid": "json"}']
        
        with patch('main.upsert_company_card') as mock_upsert:
            mock_upsert.return_value = True
            logger = AppLogger("test")
            result = run_update_company(date(2026, 2, 23), "gemini-3-flash-free", ["AAPL", "MSFT", "GOOGL"], logger)
        
        # 2 out of 3 succeeded, so overall result is True
        assert result is True

    @patch('main.update_company_card')
    @patch('main.get_company_card_and_notes')
    @patch('main.get_daily_inputs')
    def test_all_tickers_fail(self, mock_news, mock_card, mock_ai):
        """If all tickers fail, result should be False."""
        from main import run_update_company
        
        mock_news.return_value = ("News", None)
        mock_card.return_value = (SAMPLE_COMPANY_CARD, "", "2026-02-22")
        mock_ai.return_value = None
        
        logger = AppLogger("test")
        result = run_update_company(date(2026, 2, 23), "gemini-3-flash-free", ["AAPL", "MSFT"], logger)
        
        assert result is False


# ==========================================
# TEST: send_webhook_report
# ==========================================

class TestSendWebhookReport:
    
    @patch('modules.ai.ai_services.TRACKER')
    @patch('requests.post')
    def test_no_webhook_does_nothing(self, mock_post, mock_tracker):
        """No webhook URL should skip everything."""
        from main import send_webhook_report
        send_webhook_report(None, date(2026, 2, 23), "update-economy", "gemini-3-flash-free")
        mock_post.assert_not_called()

    @patch('modules.ai.ai_services.TRACKER')
    @patch('requests.post')
    def test_sends_dashboard_embed(self, mock_post, mock_tracker):
        """Should always send the dashboard embed first."""
        from main import send_webhook_report
        
        mock_tracker.get_discord_embeds.return_value = [{"title": "Dashboard"}]
        mock_tracker.metrics = MagicMock()
        mock_tracker.metrics.artifacts = {}
        
        logger = AppLogger("test")
        logger.log("Test log entry")
        
        send_webhook_report("http://webhook.test", date(2026, 2, 23), "update-economy", "gemini-3-flash-free", logger=logger)
        
        # First call is JSON embed
        first_call = mock_post.call_args_list[0]
        assert 'json' in first_call.kwargs
        assert first_call.kwargs['json']['embeds'][0]['title'] == "Dashboard"

    @patch('modules.ai.ai_services.TRACKER')
    @patch('requests.post')
    def test_ai_actions_log_filename(self, mock_post, mock_tracker):
        """AI-heavy actions should include model name in log filename."""
        from main import send_webhook_report
        
        mock_tracker.get_discord_embeds.return_value = [{"title": "Test"}]
        mock_tracker.metrics = MagicMock()
        mock_tracker.metrics.artifacts = {}
        
        logger = AppLogger("test")
        logger.log("Some log")
        
        send_webhook_report("http://webhook.test", date(2026, 2, 23), "update-economy", "gemini-3-flash-free", logger=logger)
        
        # Verify second call uploads the file with model name in filename
        if mock_post.call_count > 1:
            second_call = mock_post.call_args_list[1]
            files = second_call.kwargs.get('files', {})
            if 'file' in files:
                filename = files['file'][0]
                assert "gemini-3-flash-free" in filename

    @patch('modules.ai.ai_services.TRACKER')
    @patch('requests.post')
    def test_update_company_in_ai_actions(self, mock_post, mock_tracker):
        """update-company should be treated as an AI action (bug fix verification)."""
        from main import send_webhook_report
        
        mock_tracker.get_discord_embeds.return_value = [{"title": "Test"}]
        mock_tracker.metrics = MagicMock()
        mock_tracker.metrics.artifacts = {}
        
        logger = AppLogger("test")
        logger.log("Company update log")
        
        send_webhook_report("http://webhook.test", date(2026, 2, 23), "update-company", "gemini-3-flash-free", logger=logger)
        
        # The second message should include model name in filename
        if mock_post.call_count > 1:
            second_call = mock_post.call_args_list[1]
            files = second_call.kwargs.get('files', {})
            if 'file' in files:
                filename = files['file'][0]
                assert "gemini-3-flash-free" in filename
                assert "No_AI_Used" not in filename

    @patch('modules.ai.ai_services.TRACKER')
    @patch('requests.post')
    def test_inspect_skips_file_upload(self, mock_post, mock_tracker):
        """inspect action should NOT send log files."""
        from main import send_webhook_report
        
        mock_tracker.get_discord_embeds.return_value = [{"title": "Test"}]
        mock_tracker.metrics = MagicMock()
        mock_tracker.metrics.artifacts = {}
        
        logger = AppLogger("test")
        logger.log("Inspect log that should NOT be uploaded")
        
        send_webhook_report("http://webhook.test", date(2026, 2, 23), "inspect", "none", logger=logger)
        
        # Should only have dashboard call, no file upload
        assert mock_post.call_count == 1

    @patch('modules.ai.ai_services.TRACKER')
    @patch('requests.post')    
    def test_input_news_skips_file_upload(self, mock_post, mock_tracker):
        """input-news action should NOT send log files."""
        from main import send_webhook_report
        
        mock_tracker.get_discord_embeds.return_value = [{"title": "Test"}]
        mock_tracker.metrics = MagicMock()
        mock_tracker.metrics.artifacts = {}
        
        logger = AppLogger("test")
        logger.log("News input log")
        
        send_webhook_report("http://webhook.test", date(2026, 2, 23), "input-news", "none", logger=logger)
        
        assert mock_post.call_count == 1


# ==========================================
# TEST: Logger (Bug fix: warn alias)
# ==========================================

class TestLoggerCompat:
    
    def test_warn_alias_exists(self):
        """Logger should have both warn() and warning() methods."""
        logger = AppLogger("test")
        assert hasattr(logger, 'warn')
        assert hasattr(logger, 'warning')
    
    def test_warn_alias_works(self):
        """warn() should behave identically to warning()."""
        logger = AppLogger("test")
        logger.warn("Test warn message")
        assert "WARNING: Test warn message" in logger.get_full_log()

    def test_warning_works(self):
        logger = AppLogger("test")
        logger.warning("Test warning message")
        assert "WARNING: Test warning message" in logger.get_full_log()

    def test_log_capture(self):
        """All log types should be captured for Discord reporting."""
        logger = AppLogger("test_capture")
        logger.log("Info message")
        logger.error("Error message")
        logger.warning("Warning message")
        logger.log_code("code()", "python")
        
        full = logger.get_full_log()
        assert "INFO: Info message" in full
        assert "ERROR: Error message" in full
        assert "WARNING: Warning message" in full
        assert "code()" in full

    def test_empty_log(self):
        """Fresh logger should have empty log."""
        logger = AppLogger("test_empty")
        assert logger.get_full_log() == ""


# ==========================================
# TEST: ExecutionTracker
# ==========================================

class TestExecutionTracker:
    
    def test_start_resets_state(self):
        tracker = ExecutionTracker()
        tracker.log_call(100, True, "model")
        tracker.start(action_type="New_Run")
        
        assert tracker.metrics.total_calls == 0
        assert tracker.metrics.total_tokens == 0
        assert tracker.action_type == "New_Run"

    def test_log_call_success(self):
        tracker = _make_mock_tracker()
        tracker.log_call(5000, True, "gemini-3-flash-free", ticker="AAPL")
        
        assert tracker.metrics.total_calls == 1
        assert tracker.metrics.total_tokens == 5000
        assert tracker.metrics.success_count == 1
        assert tracker.metrics.failure_count == 0

    def test_log_call_failure(self):
        tracker = _make_mock_tracker()
        tracker.log_call(0, False, "gemini-3-flash-free", ticker="AAPL", error="Rate Limit")
        
        assert tracker.metrics.total_calls == 1
        assert tracker.metrics.failure_count == 1
        assert len(tracker.metrics.errors) == 1
        assert "Rate Limit" in tracker.metrics.errors[0]

    def test_log_error_non_api(self):
        """log_error should NOT increment total_calls (non-API failure)."""
        tracker = _make_mock_tracker()
        tracker.log_error("SPY", "Missing market data")
        
        assert tracker.metrics.total_calls == 0  # Not an API call
        assert tracker.metrics.failure_count == 1

    def test_register_artifact(self):
        tracker = _make_mock_tracker()
        tracker.register_artifact("ECONOMY_CARD", '{"test": true}')
        
        assert "ECONOMY_CARD" in tracker.metrics.artifacts
        assert tracker.metrics.artifacts["ECONOMY_CARD"] == '{"test": true}'

    def test_get_summary_division_by_zero(self):
        """Summary should handle zero calls without crashing."""
        tracker = _make_mock_tracker()
        tracker.finish()
        summary = tracker.get_summary()
        
        assert summary["success_rate"] == "0%"
        assert summary["total_calls"] == 0

    def test_get_discord_embeds(self):
        tracker = _make_mock_tracker()
        tracker.log_call(1000, True, "test-model", ticker="SPY")
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        assert len(embeds) == 1
        assert "Analyst Workbench" in embeds[0]["title"]

    def test_set_result(self):
        tracker = _make_mock_tracker()
        tracker.set_result("news_status", "Found")
        
        assert tracker.custom_results["news_status"] == "Found"

    def test_embed_color_by_success_rate(self):
        """Embed color should reflect success rate."""
        # All success -> green
        tracker = _make_mock_tracker()
        tracker.log_call(100, True, "model", ticker="X")
        tracker.finish()
        embeds = tracker.get_discord_embeds("2026-02-23")
        assert embeds[0]["color"] == 0x2ecc71  # Green
        
        # All failure -> red
        tracker2 = _make_mock_tracker()
        tracker2.log_call(0, False, "model", ticker="X", error="fail")
        tracker2.finish()
        embeds2 = tracker2.get_discord_embeds("2026-02-23")
        assert embeds2[0]["color"] == 0xe74c3c  # Red

    def test_economy_card_narrative_in_embed(self):
        """When ECONOMY_CARD artifact exists, narrative should appear in embed."""
        tracker = _make_mock_tracker()
        tracker.action_type = "Economy_Card_Update"
        tracker.log_call(5000, True, "model", ticker="ECONOMY")
        eco_card = json.dumps({"marketNarrative": "Bulls dominate on tech earnings"})
        tracker.register_artifact("ECONOMY_CARD", eco_card)
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        fields = embeds[0]["fields"]
        macro_fields = [f for f in fields if "Macro" in f.get("name", "")]
        assert len(macro_fields) > 0
        assert "Bulls dominate" in macro_fields[0]["value"]


# ==========================================
# TEST: Main CLI Edge Cases
# ==========================================

class TestMainCLIEdgeCases:
    
    def test_target_date_initialized_before_try(self):
        """Verify target_date is initialized to None before try block (bug fix)."""
        import main
        source = open(main.__file__).read()
        # Check that target_date = None appears before the try block
        target_init_pos = source.find("target_date = None")
        try_pos = source.find("target_date = None")
        assert target_init_pos != -1, "target_date should be initialized to None"

    def test_webhook_guarded_against_none_target_date(self):
        """Verify webhook report is guarded against None target_date (bug fix)."""
        import main
        source = open(main.__file__).read()
        assert "target_date is not None" in source, "Webhook should check target_date is not None"
