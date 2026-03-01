
import os
import json
import pytest
from datetime import date
from unittest.mock import MagicMock, patch
import modules.ai.ai_services as ai
from modules.analysis.impact_engine import get_or_compute_context

# --- 1. JSON Parsing Vulnerability Test ---
def test_malformed_json_response_failure(caplog):
    """Confirm the system handles malformed JSON from the AI gracefully (or fails as expected)."""
    ticker = "AAPL"
    today = date(2024, 1, 1)
    
    with patch('modules.ai.ai_services.call_gemini_api') as mock_api:
        # Simulate AI returning corrupted JSON
        mock_api.return_value = "This is not JSON: { 'invalid': 'format' }"
        
        # We need to ensure AppLogger is used to capture logs if necessary
        from modules.core.logger import AppLogger
        logger = AppLogger()
        
        result = ai.update_company_card(ticker, ai.DEFAULT_COMPANY_OVERVIEW_JSON, "2023-12-31", "", today, "model", "News", logger=logger)
        
        assert result is None

# --- 2. Direct DB Fetch Test ---
@patch('modules.analysis.impact_engine.get_session_bars_from_db')
@patch('modules.analysis.impact_engine.get_previous_session_stats')
def test_always_fetches_from_db(mock_stats, mock_bars):
    """
    Confirm that get_or_compute_context always queries the DB directly.
    No caching — every call fetches fresh data.
    """
    ticker = "TEST"
    date_str = "2024-01-01"
    
    # DB returns no data
    mock_bars.return_value = None
    mock_stats.return_value = {"yesterday_close": 0, "yesterday_high": 0, "yesterday_low": 0}
    
    logger = MagicMock()
    
    # First call hits DB
    result = get_or_compute_context(None, ticker, date_str, logger)
    mock_bars.assert_called_once()
    
    # Second call also hits DB (no caching)
    result = get_or_compute_context(None, ticker, date_str, logger)
    assert mock_bars.call_count == 2

# --- 3. Same-Date Overwrite Test ---
def test_todays_action_same_date_overwrites():
    """Re-running for the same date should overwrite the previous entry with latest data."""
    ticker = "AAPL"
    today = date(2024, 1, 1)
    
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
        "todaysAction": "Action 1"
    }
    mock_resp2 = mock_resp1.copy()
    mock_resp2["todaysAction"] = "Action 2 (Updated)"
    
    with patch('modules.ai.ai_services.call_gemini_api') as mock_api, \
         patch('modules.ai.ai_services.get_or_compute_context') as mock_context:
        
        mock_context.return_value = {"meta": {"data_points": 0}}
        
        # First Run
        mock_api.return_value = json.dumps(mock_resp1)
        card1 = ai.update_company_card(ticker, ai.DEFAULT_COMPANY_OVERVIEW_JSON, "2023-12-31", "", today, "model", "News")
        
        # Second Run (Same Date)
        mock_api.return_value = json.dumps(mock_resp2)
        card2 = ai.update_company_card(ticker, card1, "2024-01-01", "", today, "model", "News")
        
        card2_dict = json.loads(card2)
        log = card2_dict['technicalStructure']['keyActionLog']

        # Re-running for the same date overwrites the original entry.
        date_entries = [e for e in log if e['date'] == today.isoformat()]
        assert len(date_entries) == 1
        assert date_entries[0]['action'] == "Action 2 (Updated)", (
            f"Re-run should overwrite, but got '{date_entries[0]['action']}'"
        )

# --- 4. Discord Orchestration "Fire & Forget" Failure Test ---
# Note: pytest-asyncio is not installed; we drive the coroutine with asyncio.run().
def test_discord_github_dispatch_error_reporting():
    """Confirm the Discord bot correctly identifies when GitHub REFUSES the dispatch."""
    import asyncio
    from unittest.mock import AsyncMock
    import discord_bot.bot as bot

    async def _run():
        # Simulate GitHub 401 Unauthorized.
        # resp.text() is awaited inside dispatch_github_action, so it must be
        # an AsyncMock.
        mock_response = MagicMock()
        mock_response.status = 401
        mock_response.text = AsyncMock(return_value='Unauthorized')
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch('discord_bot.bot.GITHUB_TOKEN', 'fake_token'), \
             patch('discord_bot.bot.GITHUB_REPO', 'fake_repo'), \
             patch('discord_bot.bot.WORKFLOW_FILENAME', 'fake.yml'), \
             patch('aiohttp.ClientSession.post', return_value=mock_cm):
            return await bot.dispatch_github_action({"test": "data"})

    success, error, run_url = asyncio.run(_run())

    assert success is False
    assert "GitHub Error 401" in error
    assert run_url is None
