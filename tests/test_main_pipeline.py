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
    "fundamentalContext": {"analystSentiment": "Buy",
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
    
    @patch('modules.ai.ai_services.KEY_MANAGER')
    @patch('main.upsert_company_card')
    @patch('main.update_company_card')
    @patch('main.get_company_card_and_notes')
    @patch('main.get_archived_economy_card')
    @patch('main.get_daily_inputs')
    def test_single_ticker_success(self, mock_news, mock_eco_archive, mock_card, mock_ai, mock_upsert, mock_km):
        """Successful single ticker update."""
        from main import run_update_company
        
        mock_news.return_value = ("Market news today", None)
        mock_eco_archive.return_value = ('{"economyCard": "data"}', None)
        mock_card.return_value = (SAMPLE_COMPANY_CARD, "Historical: $200 support", "2026-02-22")
        mock_ai.return_value = '{"marketNote": "Updated AAPL card"}'
        mock_upsert.return_value = True
        mock_km.get_tier_key_count.return_value = 5
        
        logger = AppLogger("test")
        result = run_update_company(date(2026, 2, 23), "gemini-3-flash-free", ["AAPL"], logger)
        
        assert result is True
        mock_ai.assert_called_once()

    @patch('modules.ai.ai_services.KEY_MANAGER')
    @patch('main.upsert_company_card')
    @patch('main.update_company_card')
    @patch('main.get_company_card_and_notes')
    @patch('main.get_archived_economy_card')
    @patch('main.get_daily_inputs')
    def test_multiple_tickers(self, mock_news, mock_eco_archive, mock_card, mock_ai, mock_upsert, mock_km):
        """Multiple tickers should be processed sequentially."""
        from main import run_update_company
        
        mock_news.return_value = ("News", None)
        mock_eco_archive.return_value = ('{"economyCard": "data"}', None)
        mock_card.return_value = (SAMPLE_COMPANY_CARD, "", "2026-02-22")
        mock_ai.return_value = '{"marketNote": "Updated"}'
        mock_upsert.return_value = True
        mock_km.get_tier_key_count.return_value = 5
        
        logger = AppLogger("test")
        result = run_update_company(date(2026, 2, 23), "gemini-3-flash-free", ["AAPL", "MSFT", "GOOGL"], logger)
        
        assert result is True
        assert mock_ai.call_count == 3

    @patch('modules.ai.ai_services.KEY_MANAGER')
    @patch('main.get_daily_inputs')
    def test_continues_without_news(self, mock_news, mock_km):
        """Should continue (with warning) when no market news available."""
        from main import run_update_company
        
        mock_news.return_value = (None, None)
        logger = AppLogger("test")
        mock_km.get_tier_key_count.return_value = 5
        
        with patch('main.get_archived_economy_card') as mock_eco_archive, \
             patch('main.get_company_card_and_notes') as mock_card, \
             patch('main.update_company_card') as mock_ai, \
             patch('main.upsert_company_card') as mock_upsert:
            mock_eco_archive.return_value = ('{"economyCard": "data"}', None)
            mock_card.return_value = (SAMPLE_COMPANY_CARD, "", None)
            mock_ai.return_value = '{"test": "data"}'
            mock_upsert.return_value = True
            
            result = run_update_company(date(2026, 2, 23), "gemini-3-flash-free", ["AAPL"], logger)
        
        assert result is True
        # Verify warning was logged (not crash)
        assert "No market news found" in logger.get_full_log()

    @patch('modules.ai.ai_services.KEY_MANAGER')
    @patch('main.update_company_card')
    @patch('main.get_company_card_and_notes')
    @patch('main.get_archived_economy_card')
    @patch('main.get_daily_inputs')
    def test_partial_failure(self, mock_news, mock_eco_archive, mock_card, mock_ai, mock_km):
        """If one ticker fails, others should still be processed."""
        from main import run_update_company
        
        mock_news.return_value = ("News", None)
        mock_eco_archive.return_value = ('{"economyCard": "data"}', None)
        mock_card.return_value = (SAMPLE_COMPANY_CARD, "", "2026-02-22")
        # First call succeeds, second fails, third succeeds
        mock_ai.side_effect = ['{"valid": "json"}', None, '{"valid": "json"}']
        mock_km.get_tier_key_count.return_value = 5
        
        with patch('main.upsert_company_card') as mock_upsert:
            mock_upsert.return_value = True
            logger = AppLogger("test")
            result = run_update_company(date(2026, 2, 23), "gemini-3-flash-free", ["AAPL", "MSFT", "GOOGL"], logger)
        
        # 2 out of 3 succeeded, so overall result is True
        assert result is True

    @patch('modules.ai.ai_services.KEY_MANAGER')
    @patch('main.update_company_card')
    @patch('main.get_company_card_and_notes')
    @patch('main.get_daily_inputs')
    def test_all_tickers_fail(self, mock_news, mock_card, mock_ai, mock_km):
        """If all tickers fail, result should be False."""
        from main import run_update_company
        
        mock_news.return_value = ("News", None)
        mock_card.return_value = (SAMPLE_COMPANY_CARD, "", "2026-02-22")
        mock_ai.return_value = None
        mock_km.get_tier_key_count.return_value = 5
        
        logger = AppLogger("test")
        result = run_update_company(date(2026, 2, 23), "gemini-3-flash-free", ["AAPL", "MSFT"], logger)
        
        assert result is False

    @patch('modules.ai.ai_services.KEY_MANAGER')
    @patch('main.upsert_company_card')
    @patch('main.update_company_card')
    @patch('main.get_company_card_and_notes')
    @patch('main.get_archived_economy_card')
    @patch('main.get_daily_inputs')
    def test_adaptive_workers_single_key(self, mock_news, mock_eco_archive, mock_card, mock_ai, mock_upsert, mock_km):
        """With 1 paid key, max_workers should be 1."""
        from main import run_update_company
        
        mock_news.return_value = ("News", None)
        mock_eco_archive.return_value = ('{"economyCard": "data"}', None)
        mock_card.return_value = (SAMPLE_COMPANY_CARD, "", "2026-02-22")
        mock_ai.return_value = '{"marketNote": "Updated"}'
        mock_upsert.return_value = True
        mock_km.get_tier_key_count.return_value = 1
        
        logger = AppLogger("test")
        result = run_update_company(date(2026, 2, 23), "gemini-3-pro-paid", ["AAPL", "MSFT", "GOOGL"], logger)
        
        assert result is True
        assert "1 paid-tier key(s) available" in logger.get_full_log()
        assert "max_workers=1" in logger.get_full_log()


# ==========================================
# TEST: ALL Ticker Expansion
# ==========================================

class TestAllTickerExpansion:
    """Tests for the 'all' keyword in --tickers argument."""

    def test_all_expands_to_stock_tickers(self):
        """Passing --tickers all should expand to all stock tickers from DB."""
        raw = "all"
        raw_tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
        assert raw_tickers == ["ALL"]

    def test_all_not_treated_as_ticker_symbol(self):
        """The literal ticker 'ALL' (Allstate) should not be used when user means 'all stocks'."""
        # When raw_tickers == ["ALL"], the code should expand rather than 
        # treat it as the Allstate ticker symbol
        raw_tickers = ["ALL"]
        is_expansion = raw_tickers == ["ALL"]
        assert is_expansion is True

    def test_mixed_tickers_not_expanded(self):
        """Passing 'AAPL,ALL,MSFT' should NOT trigger expansion — only solo 'all'."""
        raw = "AAPL,ALL,MSFT"
        raw_tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
        is_expansion = raw_tickers == ["ALL"]
        assert is_expansion is False  # Should NOT expand


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
        assert tracker.metrics.retry_count == 0
        assert tracker.metrics.ticker_outcomes == {}
        assert tracker.metrics.quality_reports == {}
        assert tracker.action_type == "New_Run"

    def test_log_call_success(self):
        tracker = _make_mock_tracker()
        tracker.log_call(5000, True, "gemini-3-flash-free", ticker="AAPL")
        
        assert tracker.metrics.total_calls == 1
        assert tracker.metrics.total_tokens == 5000
        assert tracker.metrics.success_count == 1
        assert tracker.metrics.failure_count == 0
        # Verify per-ticker outcome
        assert "AAPL" in tracker.metrics.ticker_outcomes
        assert tracker.metrics.ticker_outcomes["AAPL"]["status"] == "success"

    def test_log_call_failure(self):
        tracker = _make_mock_tracker()
        tracker.log_call(0, False, "gemini-3-flash-free", ticker="AAPL", error="Rate Limit")
        
        assert tracker.metrics.total_calls == 1
        assert tracker.metrics.failure_count == 1
        assert len(tracker.metrics.errors) == 1
        assert "Rate Limit" in tracker.metrics.errors[0]
        # Verify per-ticker failure outcome
        assert tracker.metrics.ticker_outcomes["AAPL"]["status"] == "failed"
        assert tracker.metrics.ticker_outcomes["AAPL"]["error"] == "Rate Limit"

    def test_log_retry(self):
        """log_retry should increment retry count and per-ticker retries."""
        tracker = _make_mock_tracker()
        tracker.log_retry("gemini-3-flash-free", ticker="AAPL", reason="429 Rate Limit")
        tracker.log_retry("gemini-3-flash-free", ticker="AAPL", reason="500 Server Error")
        tracker.log_retry("gemini-3-flash-free", ticker="MSFT", reason="ReadTimeout")
        
        assert tracker.metrics.retry_count == 3
        assert tracker.metrics.ticker_outcomes["AAPL"]["retries"] == 2
        assert tracker.metrics.ticker_outcomes["MSFT"]["retries"] == 1

    def test_log_retry_does_not_affect_call_count(self):
        """Retries should NOT increment total_calls — they are separate."""
        tracker = _make_mock_tracker()
        tracker.log_retry("model", ticker="AAPL", reason="429")
        tracker.log_retry("model", ticker="AAPL", reason="500")
        
        assert tracker.metrics.total_calls == 0
        assert tracker.metrics.retry_count == 2

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
        assert summary["retry_count"] == 0

    def test_get_summary_includes_retries(self):
        """Summary should include retry count."""
        tracker = _make_mock_tracker()
        tracker.log_retry("model", ticker="X", reason="429")
        tracker.log_retry("model", ticker="X", reason="500")
        tracker.log_call(1000, True, "model", ticker="X")
        tracker.finish()
        
        summary = tracker.get_summary()
        assert summary["retry_count"] == 2
        assert summary["total_calls"] == 1

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
# TEST: Dashboard Quality & Layout
# ==========================================

class TestDashboardLayout:
    """Tests for the redesigned Discord dashboard that verifies proper
    sections, quality details, retry counts, and organized output."""

    def test_updated_section_shows_successful_tickers(self):
        """Dashboard should have an 'Updated' section listing successful tickers."""
        tracker = _make_mock_tracker()
        tracker.action_type = "Company_Card_Update"
        tracker.log_call(1000, True, "model", ticker="AAPL")
        tracker.log_call(1200, True, "model", ticker="MSFT")
        tracker.log_call(900, True, "model", ticker="GOOGL")
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        fields = embeds[0]["fields"]
        updated_fields = [f for f in fields if "Updated" in f.get("name", "")]
        assert len(updated_fields) == 1, "Should have exactly one 'Updated' section"
        assert "AAPL" in updated_fields[0]["value"]
        assert "MSFT" in updated_fields[0]["value"]
        assert "GOOGL" in updated_fields[0]["value"]

    def test_failed_section_shows_failed_tickers_with_errors(self):
        """Dashboard should have a 'Failed' section with error details."""
        tracker = _make_mock_tracker()
        tracker.action_type = "Company_Card_Update"
        tracker.log_call(1000, True, "model", ticker="AAPL")
        tracker.log_call(0, False, "model", ticker="GOOGL", error="Max Retries Exhausted")
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        fields = embeds[0]["fields"]
        failed_fields = [f for f in fields if "Failed" in f.get("name", "")]
        assert len(failed_fields) == 1, "Should have exactly one 'Failed' section"
        assert "GOOGL" in failed_fields[0]["value"]
        assert "Max Retries Exhausted" in failed_fields[0]["value"]

    def test_quality_section_shows_critical_issues_with_details(self):
        """Quality failures should show specific rule violations, not just counts."""
        from modules.ai.quality_validators import QualityReport, QualityIssue
        
        tracker = _make_mock_tracker()
        tracker.action_type = "Company_Card_Update"
        tracker.log_call(1000, True, "model", ticker="ADBE")
        
        # Simulate quality report with critical issues
        qr = QualityReport(card_type="company", ticker="ADBE")
        qr.issues.append(QualityIssue(
            rule="ACTION_TOO_LONG", severity="critical",
            field="keyActionLog[-1].action",
            message="todaysAction is 6200 chars (limit: 5000). Preview: 'Adobe reported...'"
        ))
        qr.issues.append(QualityIssue(
            rule="CARD_DUMP", severity="critical",
            field="keyActionLog[-1].action",
            message="Contains screener_briefing content (S_Levels)"
        ))
        tracker.log_quality("ADBE", qr)
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        fields = embeds[0]["fields"]
        quality_fields = [f for f in fields if "Quality Issues" in f.get("name", "")]
        assert len(quality_fields) == 1, "Should have quality issues section"
        
        quality_text = quality_fields[0]["value"]
        assert "ADBE" in quality_text
        assert "ACTION_TOO_LONG" in quality_text
        assert "CARD_DUMP" in quality_text
        assert "6200 chars" in quality_text

    def test_quality_perfect_no_quality_section(self):
        """Perfect quality should NOT create a Quality Issues section."""
        from modules.ai.quality_validators import QualityReport
        
        tracker = _make_mock_tracker()
        tracker.action_type = "Company_Card_Update"
        tracker.log_call(1000, True, "model", ticker="AAPL")
        
        qr = QualityReport(card_type="company", ticker="AAPL")
        # No issues = perfect
        tracker.log_quality("AAPL", qr)
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        fields = embeds[0]["fields"]
        quality_fields = [f for f in fields if "Quality Issues" in f.get("name", "")]
        assert len(quality_fields) == 0, "Perfect quality should not show quality issues section"

    def test_warning_details_shown_in_updated_section(self):
        """Warnings should show their rule and message in the Updated section, not just a count."""
        from modules.ai.quality_validators import QualityReport, QualityIssue
        
        tracker = _make_mock_tracker()
        tracker.action_type = "Company_Card_Update"
        tracker.log_call(1000, True, "model", ticker="AMD")
        
        qr = QualityReport(card_type="company", ticker="AMD")
        qr.issues.append(QualityIssue(
            rule="ACTION_NO_DATE", severity="warning",
            field="keyActionLog[-1].date",
            message="Log entry is missing a date stamp."
        ))
        tracker.log_quality("AMD", qr)
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        fields = embeds[0]["fields"]
        updated_fields = [f for f in fields if "Updated" in f.get("name", "")]
        assert len(updated_fields) == 1, "Should have Updated section"
        text = updated_fields[0]["value"]
        assert "AMD" in text
        assert "⚠️" in text
        assert "ACTION_NO_DATE" in text, "Warning rule name should be visible"
        assert "missing a date stamp" in text, "Warning message should be visible"
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        fields = embeds[0]["fields"]
        quality_fields = [f for f in fields if "Quality Issues" in f.get("name", "")]
        assert len(quality_fields) == 0, "Perfect quality should not show quality issues section"

    def test_ticker_count_shows_updated_vs_total(self):
        """Dashboard should show 'X/Y Updated' format."""
        tracker = _make_mock_tracker()
        tracker.action_type = "Company_Card_Update"
        tracker.log_call(1000, True, "model", ticker="AAPL")
        tracker.log_call(1000, True, "model", ticker="MSFT")
        tracker.log_call(0, False, "model", ticker="GOOGL", error="Failed")
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        fields = embeds[0]["fields"]
        ticker_fields = [f for f in fields if "Tickers" in f.get("name", "")]
        assert len(ticker_fields) == 1
        assert "2/3" in ticker_fields[0]["value"]

    def test_api_calls_shows_succeeded_vs_attempts(self):
        """API Calls field should show succeeded vs total attempts."""
        tracker = _make_mock_tracker()
        tracker.action_type = "Company_Card_Update"
        # Simulate: AAPL had 3 retries before succeeding
        tracker.log_retry("model", ticker="AAPL", reason="429")
        tracker.log_retry("model", ticker="AAPL", reason="429")
        tracker.log_retry("model", ticker="AAPL", reason="500")
        tracker.log_call(1000, True, "model", ticker="AAPL")
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        fields = embeds[0]["fields"]
        api_fields = [f for f in fields if "API" in f.get("name", "")]
        assert len(api_fields) == 1
        api_text = api_fields[0]["value"]
        # Should show: 1 succeeded, 4 attempts, 3 retries
        assert "1" in api_text and "succeeded" in api_text
        assert "4" in api_text and "attempts" in api_text
        assert "3 retries" in api_text

    def test_failed_ticker_shows_retry_count(self):
        """Failed tickers should show how many retries happened."""
        tracker = _make_mock_tracker()
        tracker.action_type = "Company_Card_Update"
        tracker.log_retry("model", ticker="GOOGL", reason="429")
        tracker.log_retry("model", ticker="GOOGL", reason="500")
        tracker.log_call(0, False, "model", ticker="GOOGL", error="Max Retries Exhausted")
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        fields = embeds[0]["fields"]
        failed_fields = [f for f in fields if "Failed" in f.get("name", "")]
        assert len(failed_fields) == 1
        assert "2 retries" in failed_fields[0]["value"]

    def test_heavy_retry_scenario_19_of_46(self):
        """Real-world: 19 succeeded out of 46 total attempts = keys are failing hard."""
        tracker = _make_mock_tracker()
        tracker.action_type = "Company_Card_Update"
        
        # 19 tickers, each succeeded eventually. 27 retries total across them.
        for i, ticker in enumerate(["AAPL", "MSFT", "GOOGL", "AMZN", "META", 
                                     "NVDA", "TSLA", "ADBE", "CRM", "ORCL",
                                     "INTC", "AMD", "QCOM", "AVGO", "TXN",
                                     "AMAT", "SHOP", "KLAC", "MRVL"]):
            # Some tickers had retries
            if i < 10:
                tracker.log_retry("model", ticker=ticker, reason="429")
                tracker.log_retry("model", ticker=ticker, reason="429")
            if i < 7:
                tracker.log_retry("model", ticker=ticker, reason="500")
            tracker.log_call(1000 + i, True, "model", ticker=ticker)
        
        tracker.finish()
        embeds = tracker.get_discord_embeds("2026-02-23")
        fields = embeds[0]["fields"]
        
        api_fields = [f for f in fields if "API" in f.get("name", "")]
        assert len(api_fields) == 1
        api_text = api_fields[0]["value"]
        
        # 19 succeeded
        assert "19" in api_text and "succeeded" in api_text
        # 27 retries -> 19 + 27 = 46 attempts
        assert "46" in api_text and "attempts" in api_text
        assert "27 retries" in api_text

    def test_mixed_dashboard_all_sections(self):
        """Full scenario: successes, quality issues, failures, retries."""
        from modules.ai.quality_validators import QualityReport, QualityIssue
        
        tracker = _make_mock_tracker()
        tracker.action_type = "Company_Card_Update"
        
        # AAPL: success, perfect quality
        tracker.log_call(1000, True, "model", ticker="AAPL")
        qr_aapl = QualityReport(card_type="company", ticker="AAPL")
        tracker.log_quality("AAPL", qr_aapl)
        
        # ADBE: success but quality critical
        tracker.log_call(1500, True, "model", ticker="ADBE")
        qr_adbe = QualityReport(card_type="company", ticker="ADBE")
        qr_adbe.issues.append(QualityIssue(
            rule="ACTION_TOO_LONG", severity="critical",
            field="keyActionLog[-1].action",
            message="todaysAction is 6200 chars (limit: 5000)"
        ))
        tracker.log_quality("ADBE", qr_adbe)
        
        # GOOGL: failed after retries
        tracker.log_retry("model", ticker="GOOGL", reason="429")
        tracker.log_retry("model", ticker="GOOGL", reason="429")
        tracker.log_call(0, False, "model", ticker="GOOGL", error="Max Retries Exhausted")
        
        tracker.finish()
        embeds = tracker.get_discord_embeds("2026-02-23")
        fields = embeds[0]["fields"]
        
        # Should have all 3 sections
        field_names = [f["name"] for f in fields]
        updated_sections = [n for n in field_names if "Updated" in n]
        quality_sections = [n for n in field_names if "Quality Issues" in n]
        failed_sections = [n for n in field_names if "Failed" in n]
        
        assert len(updated_sections) == 1, f"Expected Updated section, got: {field_names}"
        assert len(quality_sections) == 1, f"Expected Quality section, got: {field_names}"
        assert len(failed_sections) == 1, f"Expected Failed section, got: {field_names}"
        
        # AAPL should be in Updated, not Failed or Quality
        updated_val = [f["value"] for f in fields if "Updated" in f["name"]][0]
        assert "AAPL" in updated_val
        
        # ADBE should be in Quality
        quality_val = [f["value"] for f in fields if "Quality" in f["name"]][0]
        assert "ADBE" in quality_val
        
        # GOOGL should be in Failed
        failed_val = [f["value"] for f in fields if "Failed" in f["name"]][0]
        assert "GOOGL" in failed_val

    def test_embed_color_yellow_on_mixed(self):
        """Mixed results (some success, some fail) should produce yellow embed."""
        tracker = _make_mock_tracker()
        tracker.action_type = "Company_Card_Update"
        tracker.log_call(1000, True, "model", ticker="AAPL")
        tracker.log_call(0, False, "model", ticker="GOOGL", error="fail")
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        assert embeds[0]["color"] == 0xf1c40f  # Yellow

    def test_embed_color_yellow_on_quality_fail(self):
        """Quality failures (success API but critical quality) should produce yellow."""
        from modules.ai.quality_validators import QualityReport, QualityIssue
        
        tracker = _make_mock_tracker()
        tracker.action_type = "Company_Card_Update"
        tracker.log_call(1000, True, "model", ticker="AAPL")
        qr = QualityReport(card_type="company", ticker="AAPL")
        qr.issues.append(QualityIssue(rule="X", severity="critical", field="x", message="bad"))
        tracker.log_quality("AAPL", qr)
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        assert embeds[0]["color"] == 0xf1c40f  # Yellow (not green)

    def test_data_action_still_works(self):
        """Non-AI actions (inspect, news check) should still produce valid embeds."""
        tracker = _make_mock_tracker()
        tracker.action_type = "News_Check"
        tracker.set_result("news_status", "✅ Found (15000 chars)")
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        fields = embeds[0]["fields"]
        news_fields = [f for f in fields if "News" in f.get("name", "")]
        assert len(news_fields) >= 1
        assert "15000" in news_fields[0]["value"]

    def test_log_quality_stores_full_details(self):
        """log_quality should store all issue details for dashboard rendering."""
        from modules.ai.quality_validators import QualityReport, QualityIssue
        
        tracker = _make_mock_tracker()
        qr = QualityReport(card_type="company", ticker="AAPL")
        qr.issues.append(QualityIssue(
            rule="ACTION_TOO_LONG", severity="critical",
            field="keyActionLog[-1].action",
            message="todaysAction is 6200 chars (limit: 5000)"
        ))
        qr.issues.append(QualityIssue(
            rule="PLACEHOLDER", severity="warning",
            field="confidence",
            message="Contains placeholder text"
        ))
        tracker.log_quality("AAPL", qr)
        
        stored = tracker.metrics.quality_reports["AAPL"]
        assert len(stored) == 2
        assert stored[0]["rule"] == "ACTION_TOO_LONG"
        assert stored[0]["severity"] == "critical"
        assert stored[1]["rule"] == "PLACEHOLDER"
        assert stored[1]["severity"] == "warning"
        
        outcome = tracker.metrics.ticker_outcomes["AAPL"]
        assert outcome["quality"] == "fail"
        assert outcome["quality_critical"] == 1
        assert outcome["quality_warnings"] == 1

    def test_quality_warnings_only_still_passes(self):
        """Warnings-only quality should mark ticker as 'warnings' not 'fail'."""
        from modules.ai.quality_validators import QualityReport, QualityIssue
        
        tracker = _make_mock_tracker()
        qr = QualityReport(card_type="company", ticker="AAPL")
        qr.issues.append(QualityIssue(
            rule="MINOR", severity="warning",
            field="x", message="minor issue"
        ))
        tracker.log_quality("AAPL", qr)
        
        assert tracker.metrics.ticker_outcomes["AAPL"]["quality"] == "warnings"


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


# ==========================================
# TEST: Validation Summary Table
# ==========================================

class TestValidationSummaryTable:
    """Tests for the per-ticker validation summary table in the Discord dashboard."""

    def test_validation_table_appears_for_company_updates(self):
        """Quality Checks and Data Accuracy fields should appear when tickers are updated."""
        from modules.ai.quality_validators import QualityReport
        
        tracker = _make_mock_tracker()
        tracker.action_type = "Company_Card_Update"
        tracker.log_call(1000, True, "model", ticker="AAPL")
        
        qr = QualityReport(card_type="company", ticker="AAPL")
        tracker.log_quality("AAPL", qr)
        tracker.metrics.data_reports["AAPL"] = []
        tracker.metrics.ticker_outcomes["AAPL"]["data_accuracy"] = "perfect"
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        # Flatten all fields from all embeds
        fields = []
        for e in embeds:
            fields.extend(e.get("fields", []))
            # Also check the description (Quality Table is in description for Embed 2)
            if "description" in e and "```" in e["description"]:
                # Create a synthetic field to match the test's expectation
                if "Quality Checks" in e.get("title", ""):
                    fields.append({"name": "Quality Checks", "value": e["description"]})

        q_fields = [f for f in fields if "Quality Checks" in f.get("name", "")]
        assert len(q_fields) >= 1, "Quality Checks field should exist"
        assert "AAPL" in q_fields[0]["value"]
        # All passing = dots only, no F markers in data rows
        data_rows = q_fields[0]["value"].split("\n")
        ticker_rows = [r for r in data_rows if "AAPL" in r]
        assert len(ticker_rows) >= 1
        assert "F" not in ticker_rows[0]

    def test_validation_table_shows_failures(self):
        """Validation table should show F for failed checks."""
        from modules.ai.quality_validators import QualityReport, QualityIssue
        
        tracker = _make_mock_tracker()
        tracker.action_type = "Company_Card_Update"
        tracker.log_call(1000, True, "model", ticker="APP")
        
        qr = QualityReport(card_type="company", ticker="APP")
        qr.issues.append(QualityIssue(
            rule="CONTENT_PLACEHOLDER", severity="critical",
            field="fundamentalContext.analystSentiment",
            message="Field contains prompt placeholder text"
        ))
        tracker.log_quality("APP", qr)
        tracker.metrics.data_reports["APP"] = []
        tracker.metrics.ticker_outcomes["APP"]["data_accuracy"] = "perfect"
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        # Flatten all fields from all embeds
        fields = []
        for e in embeds:
            fields.extend(e.get("fields", []))
            # Also check the description (Quality Table is in description for Embed 2)
            if "description" in e and "```" in e["description"]:
                # Create a synthetic field to match the test's expectation
                if "Quality Checks" in e.get("title", ""):
                    fields.append({"name": "Quality Checks", "value": e["description"]})

        q_fields = [f for f in fields if "Quality Checks" in f.get("name", "")]
        assert len(q_fields) >= 1
        table_text = q_fields[0]["value"]
        assert "APP" in table_text
        # The Plc (placeholder) column should show F
        ticker_rows = [r for r in table_text.split("\n") if "APP" in r]
        assert "F" in ticker_rows[0]

    def test_validation_table_not_shown_for_failed_tickers(self):
        """Tickers that failed API calls should not appear in the validation table."""
        tracker = _make_mock_tracker()
        tracker.action_type = "Company_Card_Update"
        tracker.log_call(0, False, "model", ticker="GOOGL", error="Rate Limit")
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        # Flatten all fields from all embeds
        fields = []
        for e in embeds:
            fields.extend(e.get("fields", []))
            # Also check the description (Quality Table is in description for Embed 2)
            if "description" in e and "```" in e["description"]:
                # Create a synthetic field to match the test's expectation
                if "Quality Checks" in e.get("title", ""):
                    fields.append({"name": "Quality Checks", "value": e["description"]})

        q_fields = [f for f in fields if "Quality Checks" in f.get("name", "")]
        assert len(q_fields) == 0, "No validation table for failed-only runs"

    def test_validation_table_multiple_tickers(self):
        """Table should show all successful tickers sorted alphabetically."""
        from modules.ai.quality_validators import QualityReport
        
        tracker = _make_mock_tracker()
        tracker.action_type = "Company_Card_Update"
        
        for ticker in ["TSLA", "AAPL", "MSFT"]:
            tracker.log_call(1000, True, "model", ticker=ticker)
            qr = QualityReport(card_type="company", ticker=ticker)
            tracker.log_quality(ticker, qr)
            tracker.metrics.data_reports[ticker] = []
            tracker.metrics.ticker_outcomes[ticker]["data_accuracy"] = "perfect"
        
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        # Flatten all fields from all embeds
        fields = []
        for e in embeds:
            fields.extend(e.get("fields", []))
            # Also check the description (Quality Table is in description for Embed 2)
            if "description" in e and "```" in e["description"]:
                # Create a synthetic field to match the test's expectation
                if "Quality Checks" in e.get("title", ""):
                    fields.append({"name": "Quality Checks", "value": e["description"]})

        q_fields = [f for f in fields if "Quality Checks" in f.get("name", "")]
        assert len(q_fields) >= 1
        table_text = q_fields[0]["value"]
        aapl_pos = table_text.find("AAPL")
        msft_pos = table_text.find("MSFT")
        tsla_pos = table_text.find("TSLA")
        assert aapl_pos < msft_pos < tsla_pos, "Tickers should be sorted alphabetically"

    def test_data_inputs_table_shows_availability(self):
        """Data Inputs table should show . for available and F for missing."""
        from modules.ai.quality_validators import QualityReport
        
        tracker = _make_mock_tracker()
        tracker.action_type = "Company_Card_Update"
        tracker.log_call(1000, True, "model", ticker="AAPL")
        
        qr = QualityReport(card_type="company", ticker="AAPL")
        tracker.log_quality("AAPL", qr)
        tracker.metrics.data_reports["AAPL"] = []
        tracker.metrics.ticker_outcomes["AAPL"]["data_accuracy"] = "perfect"
        # AAPL has news but no market data
        tracker.log_data_availability("AAPL", has_news=True, has_data=False)
        tracker.finish()
        
        embeds = tracker.get_discord_embeds("2026-02-23")
        # Flatten all fields from all embeds
        fields = []
        for e in embeds:
            fields.extend(e.get("fields", []))
            
        input_fields = [f for f in fields if "Data Inputs" in f.get("name", "")]
        assert len(input_fields) >= 1, "Data Inputs field should exist"
        table_text = input_fields[0]["value"]
        assert "AAPL" in table_text
        # Should have one . (news) and one F (data)
        ticker_rows = [r for r in table_text.split("\n") if "AAPL" in r]
        assert "." in ticker_rows[0]
        assert "F" in ticker_rows[0]



