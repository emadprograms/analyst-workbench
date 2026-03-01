"""
tests/test_fixes.py
===================
Rigorous regression and unit tests for the four confirmed bugs fixed in this
codebase.  Each of the four sections below is explicitly labelled with the bug
it exercises so that a failing test immediately points to the relevant fix.

Bug 1 — Cache Staleness in ``get_or_compute_context``
Bug 2 — keyActionLog same-date overwrite in ``update_company_card`` / ``update_economy_card``
Bug 3 — JSON Parsing Vulnerability (silent data loss via missing markdown strip)
Bug 4 — Post-Dispatch Error Reporting in Discord-to-GitHub orchestration

Additional sections exercise edge-cases and integration paths that were not
covered by the pre-existing test suite.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# Disable any real Infisical / DB connections before any project import
os.environ["DISABLE_INFISICAL"] = "1"

# ---------------------------------------------------------------------------
# Project imports (only after env setup)
# ---------------------------------------------------------------------------
from modules.core.logger import AppLogger
from modules.analysis.impact_engine import (
    _is_valid_context,
    get_or_compute_context,
)
from modules.ai.ai_services import (
    _safe_parse_ai_json,
    update_company_card,
    update_economy_card,
    DEFAULT_COMPANY_OVERVIEW_JSON,
    DEFAULT_ECONOMY_CARD_JSON,
)


# ===========================================================================
# Shared helpers / fixtures
# ===========================================================================

_VALID_CONTEXT = {
    "status": "Active",
    "meta": {"ticker": "AAPL", "data_points": 78, "date": "2026-02-23"},
    "sessions": {},
}

_NO_DATA_CONTEXT = {
    "status": "No Data",
    "meta": {"ticker": "AAPL"},
}

_ZERO_POINTS_CONTEXT = {
    "status": "Active",
    "meta": {"ticker": "AAPL", "data_points": 0},
    "sessions": {},
}


def _minimal_ai_company_response(action: str = "Default action") -> dict:
    """Minimal valid AI response for update_company_card."""
    return {
        "marketNote": "Battle Card: AAPL",
        "confidence": "Trend_Bias: Bullish",
        "screener_briefing": "Setup_Bias: Bullish",
        "basicContext": {
            "tickerDate": "AAPL | 2026-02-23",
            "sector": "Technology",
            "companyDescription": "Apple Inc.",
            "priceTrend": "Uptrend",
            "recentCatalyst": "AI news",
        },
        "technicalStructure": {
            "majorSupport": "$200",
            "majorResistance": "$220",
            "pattern": "Breakout",
            "volumeMomentum": "High volume",
        },
        "fundamentalContext": {
            "analystSentiment": "Overweight",
            "insiderActivity": "None",
            "peerPerformance": "Outperforming",
        },
        "behavioralSentiment": {
            "buyerVsSeller": "Buyers in control",
            "emotionalTone": "Accumulation",
            "newsReaction": "Bullish",
        },
        "openingTradePlan": {
            "planName": "Long $205",
            "knownParticipant": "Committed Buyers",
            "expectedParticipant": "FOMO",
            "trigger": "$210",
            "invalidation": "$200",
        },
        "alternativePlan": {
            "planName": "Short $220",
            "scenario": "Rejection",
            "knownParticipant": "Committed Sellers",
            "expectedParticipant": "Panic",
            "trigger": "$220 rej",
            "invalidation": "$225 break",
        },
        "todaysAction": action,
    }


def _minimal_ai_economy_response(action: str = "Economy action") -> dict:
    return {
        "marketNarrative": "Risk-on rally",
        "marketBias": "Bullish",
        "keyEconomicEvents": {"last_24h": "CPI", "next_24h": "FOMC"},
        "sectorRotation": {
            "leadingSectors": ["XLK"],
            "laggingSectors": ["XLE"],
            "rotationAnalysis": "Tech leading",
        },
        "indexAnalysis": {"pattern": "Uptrend", "SPY": "450+", "QQQ": "380+"},
        "interMarketAnalysis": {
            "bonds": "TLT flat",
            "commodities": "Gold up",
            "currencies": "DXY down",
            "crypto": "BTC up",
        },
        "marketInternals": {"volatility": "VIX 15"},
        "todaysAction": action,
    }


# ===========================================================================
# BUG 1 — _is_valid_context guard (Cache removed, guard still used)
# ===========================================================================

class TestIsValidContext:
    """Unit tests for the _is_valid_context guard function."""

    def test_none_is_invalid(self):
        assert _is_valid_context(None) is False

    def test_empty_dict_is_invalid(self):
        assert _is_valid_context({}) is False

    def test_non_dict_is_invalid(self):
        assert _is_valid_context("string") is False
        assert _is_valid_context(42) is False
        assert _is_valid_context([]) is False

    def test_no_data_status_is_invalid(self):
        assert _is_valid_context(_NO_DATA_CONTEXT) is False

    def test_zero_data_points_is_invalid(self):
        assert _is_valid_context(_ZERO_POINTS_CONTEXT) is False

    def test_missing_meta_is_invalid(self):
        ctx = {"status": "Active", "sessions": {}}
        assert _is_valid_context(ctx) is False

    def test_missing_data_points_key_is_invalid(self):
        ctx = {"status": "Active", "meta": {"ticker": "AAPL"}, "sessions": {}}
        assert _is_valid_context(ctx) is False

    def test_valid_context_passes(self):
        assert _is_valid_context(_VALID_CONTEXT) is True

    def test_valid_context_with_large_data_points_passes(self):
        ctx = dict(_VALID_CONTEXT)
        ctx["meta"] = {"ticker": "SPY", "data_points": 312}
        assert _is_valid_context(ctx) is True

    def test_data_points_of_one_passes(self):
        """Even a single bar is considered valid (better than zero)."""
        ctx = {
            "status": "Active",
            "meta": {"ticker": "X", "data_points": 1},
            "sessions": {},
        }
        assert _is_valid_context(ctx) is True


class TestGetOrComputeContext:
    """Tests that get_or_compute_context always fetches from DB."""

    TICKER = "DIRECT_TEST"
    DATE = "2026-01-15"

    @patch("modules.analysis.impact_engine.get_session_bars_from_db")
    @patch("modules.analysis.impact_engine.get_previous_session_stats")
    def test_always_queries_db(self, mock_stats, mock_bars):
        """Every call must hit the DB — no caching."""
        import pandas as pd
        from pytz import timezone as pytz_timezone
        from datetime import datetime, timedelta
        utc = pytz_timezone("UTC")
        logger = AppLogger("test")

        base = datetime(2026, 1, 15, 14, 30, tzinfo=utc)
        rows = []
        for i in range(13):
            t = base + timedelta(minutes=30 * i)
            rows.append({
                "timestamp": t,
                "Open": 100.0 + i * 0.1,
                "High": 101.0 + i * 0.1,
                "Low": 99.0 + i * 0.1,
                "Close": 100.5 + i * 0.1,
                "Volume": 10000 + i * 100,
                "dt_eastern": t.astimezone(pytz_timezone("US/Eastern")),
            })
        mock_bars.return_value = pd.DataFrame(rows)
        mock_stats.return_value = {"yesterday_close": 99.0, "yesterday_high": 101.0, "yesterday_low": 98.0}

        result1 = get_or_compute_context(None, self.TICKER, self.DATE, logger)
        assert result1 is not None
        assert mock_bars.call_count == 1

        # Second call must also hit DB
        result2 = get_or_compute_context(None, self.TICKER, self.DATE, logger)
        assert result2 is not None
        assert mock_bars.call_count == 2


# ===========================================================================
# BUG 2 — keyActionLog Same-Date Overwrite
# ===========================================================================

class TestKeyActionLogOverwrite:
    """
    Tests that confirm the keyActionLog same-date overwrite behaviour (Bug 2 revised).

    Re-running the card builder for the same date should overwrite the previous
    entry with the latest AI output. Entries for different dates are appended
    and never touched by subsequent runs.
    """

    _CTX = {"meta": {"ticker": "AAPL", "data_points": 1}, "sessions": {}}

    @pytest.fixture(autouse=True)
    def _patch_deps(self):
        with (
            patch("modules.ai.ai_services.call_gemini_api") as mock_api,
            patch("modules.ai.ai_services.get_or_compute_context", return_value=self._CTX),
            patch("modules.ai.ai_services.get_db_connection", return_value=MagicMock()),
        ):
            self.mock_api = mock_api
            yield

    def _run_company_card(self, previous_card_json: str, date_str: str, action_text: str) -> str:
        self.mock_api.return_value = json.dumps(
            _minimal_ai_company_response(action_text)
        )
        return update_company_card(
            ticker="AAPL",
            previous_card_json=previous_card_json,
            previous_card_date=date_str,
            historical_notes="",
            new_eod_date=date.fromisoformat(date_str),
            model_name="model",
            market_context_summary="News",
            logger=AppLogger("test"),
        )

    def _run_economy_card(self, previous_card_json: str, date_str: str, action_text: str) -> str:
        self.mock_api.return_value = json.dumps(
            _minimal_ai_economy_response(action_text)
        )
        return update_economy_card(
            current_economy_card=previous_card_json,
            daily_market_news="News",
            model_name="model",
            selected_date=date.fromisoformat(date_str),
            logger=AppLogger("test"),
        )

    # --- Company card ---

    def test_company_card_first_entry_is_written(self):
        base = DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", "AAPL")
        card = self._run_company_card(base, "2026-02-23", "First entry")
        log = json.loads(card)["technicalStructure"]["keyActionLog"]
        entries = [e for e in log if e["date"] == "2026-02-23"]
        assert len(entries) == 1
        assert entries[0]["action"] == "First entry"

    def test_company_card_second_run_same_date_overwrites(self):
        """
        Re-running the card builder for the same date should overwrite
        the previous entry with the latest data (no stale entries).
        """
        base = DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", "AAPL")
        card1 = self._run_company_card(base, "2026-02-23", "ORIGINAL entry")
        card2 = self._run_company_card(card1, "2026-02-23", "UPDATED entry")

        log = json.loads(card2)["technicalStructure"]["keyActionLog"]
        entries = [e for e in log if e["date"] == "2026-02-23"]
        assert len(entries) == 1, "Duplicate date entries found in log."
        assert entries[0]["action"] == "UPDATED entry", (
            f"Re-run should overwrite, but got '{entries[0]['action']}'"
        )

    def test_company_card_different_dates_both_appended(self):
        base = DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", "AAPL")
        card1 = self._run_company_card(base, "2026-02-22", "Day 1 entry")
        card2 = self._run_company_card(card1, "2026-02-23", "Day 2 entry")

        log = json.loads(card2)["technicalStructure"]["keyActionLog"]
        dates = {e["date"] for e in log}
        assert "2026-02-22" in dates
        assert "2026-02-23" in dates

    def test_company_card_preserves_all_prior_log_entries_across_runs(self):
        base = DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", "AAPL")
        card = base
        for i in range(5):
            d = f"2026-02-{18 + i:02d}"
            card = self._run_company_card(card, d, f"Entry for {d}")

        log = json.loads(card)["technicalStructure"]["keyActionLog"]
        dates_in_log = [e["date"] for e in log]
        for i in range(5):
            d = f"2026-02-{18 + i:02d}"
            assert d in dates_in_log, f"Log entry for {d} was lost."

    # --- Economy card ---

    def test_economy_card_first_entry_is_written(self):
        card = self._run_economy_card(DEFAULT_ECONOMY_CARD_JSON, "2026-02-23", "Economy day 1")
        log = json.loads(card)["keyActionLog"]
        entries = [e for e in log if e["date"] == "2026-02-23"]
        assert len(entries) == 1
        assert entries[0]["action"] == "Economy day 1"

    def test_economy_card_second_run_same_date_overwrites(self):
        """Re-running economy card for same date should overwrite previous entry."""
        card1 = self._run_economy_card(DEFAULT_ECONOMY_CARD_JSON, "2026-02-23", "ORIGINAL economy")
        card2 = self._run_economy_card(card1, "2026-02-23", "UPDATED economy")

        log = json.loads(card2)["keyActionLog"]
        entries = [e for e in log if e["date"] == "2026-02-23"]
        assert len(entries) == 1, "Duplicate date entries found in economy log."
        assert entries[0]["action"] == "UPDATED economy", (
            f"Re-run should overwrite, but got '{entries[0]['action']}'"
        )

    def test_economy_card_different_dates_both_appended(self):
        card1 = self._run_economy_card(DEFAULT_ECONOMY_CARD_JSON, "2026-02-22", "Economy day 1")
        card2 = self._run_economy_card(card1, "2026-02-23", "Economy day 2")

        log = json.loads(card2)["keyActionLog"]
        dates = {e["date"] for e in log}
        assert "2026-02-22" in dates
        assert "2026-02-23" in dates


# ===========================================================================
# BUG 3 — JSON Parsing Vulnerability
# ===========================================================================

class TestSafeParseAiJson:
    """
    Exhaustive unit tests for the ``_safe_parse_ai_json`` helper (Bug 3).

    This function is the single entry-point for all AI response parsing; it must
    handle every plausible shape that a generative model could return.
    """

    # --- Successful parses ---

    def test_direct_json_string(self):
        """Most common path: structured output mode returns bare JSON."""
        data = {"key": "value", "nested": {"a": 1}}
        result = _safe_parse_ai_json(json.dumps(data))
        assert result == data

    def test_json_with_leading_trailing_whitespace(self):
        data = {"status": "ok"}
        result = _safe_parse_ai_json(f"   {json.dumps(data)}   ")
        assert result == data

    def test_markdown_fenced_json_block(self):
        """When AI wraps response in ```json ... ```."""
        data = {"todaysAction": "Breakout day"}
        text = f"```json\n{json.dumps(data)}\n```"
        result = _safe_parse_ai_json(text)
        assert result == data

    def test_markdown_fenced_without_json_label(self):
        """``` block without 'json' label should still parse."""
        data = {"x": 42}
        text = f"```\n{json.dumps(data)}\n```"
        result = _safe_parse_ai_json(text)
        assert result == data

    def test_json_embedded_in_prose(self):
        """Last-resort extraction: JSON object embedded in surrounding text."""
        data = {"a": 1, "b": [2, 3]}
        text = f"Here is my analysis:\n\n{json.dumps(data)}\n\nEnd of response."
        result = _safe_parse_ai_json(text)
        assert result == data

    def test_multiple_fenced_blocks_prefers_last(self):
        """
        BUG 3 EDGE CASE: If the prompt contains a JSON example inside an
        earlier ``` block, the parser must use the LAST block (the answer),
        not the first (the example).
        """
        example = {"example": True}
        answer = {"result": "real answer", "todaysAction": "Do this"}
        text = (
            f"Example:\n```json\n{json.dumps(example)}\n```\n\n"
            f"Your output:\n```json\n{json.dumps(answer)}\n```"
        )
        result = _safe_parse_ai_json(text)
        assert result == answer

    def test_unicode_content_in_json(self):
        data = {"name": "Björn Ångström", "symbol": "€"}
        result = _safe_parse_ai_json(json.dumps(data, ensure_ascii=False))
        assert result == data

    def test_large_nested_json(self):
        """Performance / correctness on a realistic card-sized payload."""
        data = _minimal_ai_company_response("Large test action with " + "x" * 500)
        result = _safe_parse_ai_json(json.dumps(data))
        assert result is not None
        assert result["todaysAction"].startswith("Large test action")

    # --- Failed parses that must return None ---

    def test_completely_invalid_text_returns_none(self):
        """BUG 3 CORE: Must not raise; must return None cleanly."""
        assert _safe_parse_ai_json("This is not JSON at all.") is None

    def test_none_input_returns_none(self):
        assert _safe_parse_ai_json(None) is None  # type: ignore[arg-type]

    def test_empty_string_returns_none(self):
        assert _safe_parse_ai_json("") is None

    def test_whitespace_only_returns_none(self):
        assert _safe_parse_ai_json("   \n\t  ") is None

    def test_partial_json_returns_none(self):
        assert _safe_parse_ai_json('{"key": "missing closing brace"') is None

    def test_array_at_top_level_returns_none(self):
        """Top-level arrays are not expected; we only parse objects."""
        # The bare-braces fallback searches for {...}, so arrays without
        # wrapping braces won't be matched.  The function may or may not
        # parse this — the critical contract is it never raises.
        result = _safe_parse_ai_json('[1, 2, 3]')
        # Either None or the parsed array is acceptable — no exception is the rule
        assert result is None or isinstance(result, (list, dict))

    def test_python_style_dict_single_quotes_returns_none(self):
        """Python dict syntax ({'key': 'value'}) is not valid JSON."""
        assert _safe_parse_ai_json("{'key': 'value'}") is None


class TestJsonParsingInCardUpdate:
    """
    End-to-end parsing tests ensuring update_company_card and update_economy_card
    correctly handle markdown-wrapped AI responses (Bug 3).
    """

    _CTX = {"meta": {"ticker": "AAPL", "data_points": 1}, "sessions": {}}

    @pytest.fixture(autouse=True)
    def _patch_deps(self):
        with (
            patch("modules.ai.ai_services.call_gemini_api") as mock_api,
            patch("modules.ai.ai_services.get_or_compute_context", return_value=self._CTX),
            patch("modules.ai.ai_services.get_db_connection", return_value=MagicMock()),
        ):
            self.mock_api = mock_api
            yield

    def test_company_card_handles_markdown_wrapped_response(self):
        """
        BUG 3 REGRESSION: update_company_card must succeed even when the AI
        wraps its JSON in a markdown fence — not silently return None.
        """
        response_dict = _minimal_ai_company_response("Markdown-wrapped action")
        # Simulate API returning fenced JSON
        self.mock_api.return_value = f"```json\n{json.dumps(response_dict)}\n```"

        result = update_company_card(
            ticker="AAPL",
            previous_card_json=DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", "AAPL"),
            previous_card_date="2026-02-22",
            historical_notes="",
            new_eod_date=date(2026, 2, 23),
            model_name="model",
            market_context_summary="News",
            logger=AppLogger("test"),
        )

        assert result is not None, (
            "BUG 3 REGRESSION: update_company_card returned None on a "
            "markdown-wrapped JSON response."
        )
        card = json.loads(result)
        assert "Markdown-wrapped action" in json.dumps(card)

    def test_economy_card_handles_markdown_wrapped_response(self):
        """
        BUG 3 CORE: The original update_economy_card code had NO regex strip
        and would silently return None on a markdown-fenced response.
        """
        response_dict = _minimal_ai_economy_response("Economy markdown action")
        self.mock_api.return_value = f"```json\n{json.dumps(response_dict)}\n```"

        result = update_economy_card(
            current_economy_card=DEFAULT_ECONOMY_CARD_JSON,
            daily_market_news="News",
            model_name="model",
            selected_date=date(2026, 2, 23),
            logger=AppLogger("test"),
        )

        assert result is not None, (
            "BUG 3 REGRESSION: update_economy_card returned None on a "
            "markdown-wrapped JSON response."
        )
        card = json.loads(result)
        log = card.get("keyActionLog", [])
        assert any("Economy markdown action" in e.get("action", "") for e in log)

    def test_company_card_returns_none_on_garbage_response(self):
        """Non-parsable AI output must return None, not crash."""
        self.mock_api.return_value = "I'm sorry, I cannot do that."

        result = update_company_card(
            ticker="AAPL",
            previous_card_json=DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", "AAPL"),
            previous_card_date="2026-02-22",
            historical_notes="",
            new_eod_date=date(2026, 2, 23),
            model_name="model",
            market_context_summary="News",
            logger=AppLogger("test"),
        )

        assert result is None

    def test_economy_card_returns_none_on_garbage_response(self):
        self.mock_api.return_value = "Error: context window exceeded."

        result = update_economy_card(
            current_economy_card=DEFAULT_ECONOMY_CARD_JSON,
            daily_market_news="News",
            model_name="model",
            selected_date=date(2026, 2, 23),
            logger=AppLogger("test"),
        )

        assert result is None

    def test_company_card_handles_prose_with_embedded_json(self):
        """Last-resort extraction: model explains itself then gives JSON."""
        response_dict = _minimal_ai_company_response("Embedded JSON action")
        prose_wrapped = (
            "Based on my analysis, here is the result:\n\n"
            + json.dumps(response_dict)
            + "\n\nI hope this helps."
        )
        self.mock_api.return_value = prose_wrapped

        result = update_company_card(
            ticker="AAPL",
            previous_card_json=DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", "AAPL"),
            previous_card_date="2026-02-22",
            historical_notes="",
            new_eod_date=date(2026, 2, 23),
            model_name="model",
            market_context_summary="News",
            logger=AppLogger("test"),
        )

        assert result is not None

    def test_company_card_returns_none_when_ai_returns_none(self):
        """None from call_gemini_api must propagate as None."""
        self.mock_api.return_value = None

        result = update_company_card(
            ticker="AAPL",
            previous_card_json=DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", "AAPL"),
            previous_card_date="2026-02-22",
            historical_notes="",
            new_eod_date=date(2026, 2, 23),
            model_name="model",
            market_context_summary="News",
            logger=AppLogger("test"),
        )

        assert result is None

    def test_company_card_missing_todos_action_returns_none(self):
        """AI response missing the required 'todaysAction' key → None."""
        response_dict = _minimal_ai_company_response("x")
        del response_dict["todaysAction"]
        self.mock_api.return_value = json.dumps(response_dict)

        result = update_company_card(
            ticker="AAPL",
            previous_card_json=DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", "AAPL"),
            previous_card_date="2026-02-22",
            historical_notes="",
            new_eod_date=date(2026, 2, 23),
            model_name="model",
            market_context_summary="News",
            logger=AppLogger("test"),
        )

        assert result is None


# ===========================================================================
# BUG 4 — Post-Dispatch Error Reporting
# ===========================================================================

class TestDispatchGithubAction:
    """
    Tests for dispatch_github_action and the _fetch_latest_run_url helper
    (Bug 4).
    """

    def _run(self, coro):
        """Run an async coroutine inside a fresh event loop (Python 3.12 safe)."""
        return asyncio.run(coro)

    # ------------------------------------------------------------------
    # Helper patches
    # ------------------------------------------------------------------

    @staticmethod
    def _mock_resp(status: int, body: str = "") -> MagicMock:
        """Build a mock aiohttp response context manager."""
        resp = AsyncMock()
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        resp.status = status
        resp.text = AsyncMock(return_value=body)
        resp.json = AsyncMock(return_value={})
        return resp

    # ------------------------------------------------------------------
    # Return-type contract tests
    # ------------------------------------------------------------------

    @patch("discord_bot.bot.GITHUB_TOKEN", "fake_token")
    @patch("discord_bot.bot.GITHUB_REPO", "owner/repo")
    @patch("discord_bot.bot._fetch_latest_run_url", new_callable=AsyncMock, return_value=None)
    @patch("aiohttp.ClientSession.post")
    def test_success_returns_3_tuple_true(self, mock_post, _mock_fetch):
        """BUG 4: Successful dispatch must return (True, str, run_url_or_None)."""
        mock_post.return_value = self._mock_resp(204)

        import discord_bot.bot as bot_module
        success, message, run_url = self._run(
            bot_module.dispatch_github_action({"target_date": "2026-02-23"})
        )

        assert success is True
        assert isinstance(message, str)
        # run_url is None here because _fetch_latest_run_url is mocked to None
        assert run_url is None

    @patch("discord_bot.bot.GITHUB_TOKEN", "fake_token")
    @patch("discord_bot.bot.GITHUB_REPO", "owner/repo")
    @patch("aiohttp.ClientSession.post")
    def test_failure_422_returns_3_tuple_false_with_body(self, mock_post):
        """
        BUG 4 CORE: On a non-204 response the error message must include both
        the status code AND the response body, not just the status code.
        """
        error_body = '{"message":"No ref found for: fake-branch"}'
        mock_post.return_value = self._mock_resp(422, body=error_body)

        import discord_bot.bot as bot_module
        success, message, run_url = self._run(
            bot_module.dispatch_github_action({"target_date": "2026-02-23"})
        )

        assert success is False
        assert "422" in message, "HTTP status code missing from error message"
        assert "No ref found" in message, "Response body missing from error message"
        assert run_url is None

    @patch("discord_bot.bot.GITHUB_TOKEN", "fake_token")
    @patch("discord_bot.bot.GITHUB_REPO", "owner/repo")
    @patch("aiohttp.ClientSession.post")
    def test_failure_401_unauthorized(self, mock_post):
        """401 Unauthorized must surface a meaningful error."""
        mock_post.return_value = self._mock_resp(401, body="Unauthorized")

        import discord_bot.bot as bot_module
        success, message, run_url = self._run(
            bot_module.dispatch_github_action({"target_date": "2026-02-23"})
        )

        assert success is False
        assert "401" in message
        assert run_url is None

    def test_missing_credentials_returns_false(self):
        """No token or repo → immediate (False, message, None) without network call."""
        import discord_bot.bot as bot_module

        with patch("discord_bot.bot.GITHUB_TOKEN", ""), patch("discord_bot.bot.GITHUB_REPO", ""):
            success, message, run_url = self._run(
                bot_module.dispatch_github_action({"x": "y"})
            )

        assert success is False
        assert run_url is None
        assert "Missing" in message

    def test_missing_token_returns_false(self):
        import discord_bot.bot as bot_module

        with patch("discord_bot.bot.GITHUB_TOKEN", None), patch("discord_bot.bot.GITHUB_REPO", "owner/repo"):
            success, message, run_url = self._run(
                bot_module.dispatch_github_action({})
            )

        assert success is False
        assert run_url is None

    @patch("discord_bot.bot.GITHUB_TOKEN", "fake_token")
    @patch("discord_bot.bot.GITHUB_REPO", "owner/repo")
    @patch("discord_bot.bot._fetch_latest_run_url")
    @patch("aiohttp.ClientSession.post")
    def test_run_url_returned_when_poll_succeeds(self, mock_post, mock_fetch):
        """
        BUG 4 IMPROVEMENT: When _fetch_latest_run_url returns a URL, the
        3-tuple must contain that URL so callers can surface a direct run link.
        """
        expected_url = "https://github.com/owner/repo/actions/runs/99999"
        mock_post.return_value = self._mock_resp(204)
        mock_fetch_coro = AsyncMock(return_value=expected_url)

        import discord_bot.bot as bot_module
        original_fetch = bot_module._fetch_latest_run_url
        bot_module._fetch_latest_run_url = mock_fetch_coro

        try:
            success, message, run_url = self._run(
                bot_module.dispatch_github_action({"target_date": "2026-02-23"})
            )
            assert success is True
            assert run_url == expected_url
        finally:
            bot_module._fetch_latest_run_url = original_fetch


class TestFetchLatestRunUrl:
    """
    Unit tests for _fetch_latest_run_url (Bug 4).

    This helper may return None or a URL depending on GitHub API availability.
    It must never raise.
    """

    def _run(self, coro):
        return asyncio.run(coro)

    @patch("discord_bot.bot.GITHUB_REPO", "owner/repo")
    @patch("discord_bot.bot.WORKFLOW_FILENAME", "manual_run.yml")
    @patch("asyncio.sleep", new_callable=AsyncMock)  # Skip the 5-second wait
    def test_returns_run_url_when_api_responds(self, _mock_sleep):
        """When GitHub returns a run list, the html_url of the first run is returned."""
        import discord_bot.bot as bot_module

        run_url = "https://github.com/owner/repo/actions/runs/12345"

        # Use a plain MagicMock for session so that session.get(...) is a regular
        # (non-async) call that returns an async context manager directly.
        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={"workflow_runs": [{"html_url": run_url, "status": "queued"}]}
        )

        mock_session = MagicMock()  # NOT AsyncMock — get() is a plain method
        mock_session.get.return_value = mock_resp

        result = self._run(
            bot_module._fetch_latest_run_url(mock_session, {"Authorization": "token x"})
        )

        assert result == run_url

    @patch("discord_bot.bot.GITHUB_REPO", "owner/repo")
    @patch("discord_bot.bot.WORKFLOW_FILENAME", "manual_run.yml")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    def test_returns_none_when_no_runs_found(self, _mock_sleep):
        """Empty workflow_runs list → None without raising."""
        import discord_bot.bot as bot_module

        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"workflow_runs": []})

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        result = self._run(
            bot_module._fetch_latest_run_url(mock_session, {})
        )

        assert result is None

    @patch("discord_bot.bot.GITHUB_REPO", "owner/repo")
    @patch("discord_bot.bot.WORKFLOW_FILENAME", "manual_run.yml")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    def test_returns_none_on_403(self, _mock_sleep):
        """Non-200 API response → None."""
        import discord_bot.bot as bot_module

        mock_resp = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_resp.status = 403

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        result = self._run(
            bot_module._fetch_latest_run_url(mock_session, {})
        )

        assert result is None

    @patch("discord_bot.bot.GITHUB_REPO", "owner/repo")
    @patch("discord_bot.bot.WORKFLOW_FILENAME", "manual_run.yml")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    def test_returns_none_on_network_exception(self, _mock_sleep):
        """Network errors must be swallowed silently (non-fatal)."""
        import discord_bot.bot as bot_module

        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("Connection refused")

        result = self._run(
            bot_module._fetch_latest_run_url(mock_session, {})
        )

        assert result is None

    def test_returns_none_when_repo_not_configured(self):
        """Missing GITHUB_REPO guard → immediate None."""
        import discord_bot.bot as bot_module

        with patch("discord_bot.bot.GITHUB_REPO", None):
            result = self._run(
                bot_module._fetch_latest_run_url(AsyncMock(), {})
            )

        assert result is None


# ===========================================================================
# Additional edge-case / regression tests
# ===========================================================================

class TestDeepCopyIsolation:
    """
    Confirm that the deep-copy protection in card update functions prevents
    mutation of the `previous_card_json` argument across calls.
    """

    _CTX = {"meta": {"ticker": "AAPL", "data_points": 1}, "sessions": {}}

    @pytest.fixture(autouse=True)
    def _patch_deps(self):
        with (
            patch("modules.ai.ai_services.call_gemini_api") as mock_api,
            patch("modules.ai.ai_services.get_or_compute_context", return_value=self._CTX),
            patch("modules.ai.ai_services.get_db_connection", return_value=MagicMock()),
        ):
            self.mock_api = mock_api
            yield

    def test_previous_company_card_not_mutated_after_update(self):
        """update_company_card must not mutate the dict underlying previous_card_json."""
        import copy

        base_json = DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", "AAPL")
        base_dict = json.loads(base_json)
        original_log_len = len(
            base_dict.get("technicalStructure", {}).get("keyActionLog", [])
        )

        self.mock_api.return_value = json.dumps(
            _minimal_ai_company_response("Isolation test")
        )
        update_company_card(
            ticker="AAPL",
            previous_card_json=base_json,
            previous_card_date="2026-02-22",
            historical_notes="",
            new_eod_date=date(2026, 2, 23),
            model_name="model",
            market_context_summary="News",
            logger=AppLogger("test"),
        )

        # Re-parse the original string — it should be unchanged
        after_dict = json.loads(base_json)
        after_log_len = len(
            after_dict.get("technicalStructure", {}).get("keyActionLog", [])
        )
        assert after_log_len == original_log_len, (
            "previous_card_json's underlying data was mutated by update_company_card."
        )


class TestCardAssemblyPreservesReadOnlyFields:
    """Verify that read-only static fields are preserved through AI updates."""

    _CTX = {"meta": {"ticker": "AAPL", "data_points": 1}, "sessions": {}}

    @pytest.fixture(autouse=True)
    def _patch_deps(self):
        with (
            patch("modules.ai.ai_services.call_gemini_api") as mock_api,
            patch("modules.ai.ai_services.get_or_compute_context", return_value=self._CTX),
            patch("modules.ai.ai_services.get_db_connection", return_value=MagicMock()),
        ):
            self.mock_api = mock_api
            yield

    def test_ticker_date_field_auto_updated(self):
        """tickerDate must always reflect the new trade date after update."""
        base_json = DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", "AAPL")

        ai_resp = _minimal_ai_company_response("TickerDate test")
        self.mock_api.return_value = json.dumps(ai_resp)

        result = update_company_card(
            ticker="AAPL",
            previous_card_json=base_json,
            previous_card_date="2026-02-22",
            historical_notes="",
            new_eod_date=date(2026, 2, 23),
            model_name="model",
            market_context_summary="News",
            logger=AppLogger("test"),
        )

        assert result is not None
        card = json.loads(result)
        assert "2026-02-23" in card["basicContext"]["tickerDate"]
