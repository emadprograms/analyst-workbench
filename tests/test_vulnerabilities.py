
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

# --- 2. Cache Invalidation Vulnerability Test ---
@patch('modules.analysis.impact_engine.get_session_bars_from_db')
@patch('modules.analysis.impact_engine.get_previous_session_stats')
@patch('os.path.exists')
@patch('builtins.open', new_callable=MagicMock)
def test_cache_staleness_logic(mock_open, mock_exists, mock_stats, mock_bars):
    """
    Confirm that if a cache file exists, it's ALWAYS used, 
    even if the DB has more data (Vulnerability).
    """
    ticker = "TEST"
    date_str = "2024-01-01"
    
    # Simulate cache HIT
    mock_exists.return_value = True
    
    # Mock file reading
    mock_file_content = {
        "meta": {"ticker": ticker, "data_points": 10},
        "sessions": {}
    }
    
    # Setup mock_open
    mock_file = MagicMock()
    mock_file.__enter__.return_value.read.return_value = json.dumps(mock_file_content)
    mock_open.return_value = mock_file
    
    logger = MagicMock()
    
    # This call should hit the cache and RETURN the 10 data points context
    result = get_or_compute_context(None, ticker, date_str, logger)
    
    assert result["meta"]["data_points"] == 10
    # Ensure it NEVER even tried to call the DB
    mock_bars.assert_not_called()
    mock_stats.assert_not_called()

# --- 3. Immutability Violation (Overwrite) Test ---
def test_todays_action_overwrite_violation():
    """Confirms that the system overwrites previous entries for the same date (Violating GEMINI.md)."""
    ticker = "AAPL"
    today = date(2024, 1, 1)
    
    mock_resp1 = {
        "marketNote": "Note 1",
        "confidence": "High",
        "screener_briefing": "...",
        "basicContext": {"tickerDate": "...", "sector": "...", "companyDescription": "...", "priceTrend": "...", "recentCatalyst": "..."},
        "technicalStructure": {"majorSupport": "...", "majorResistance": "...", "pattern": "...", "volumeMomentum": "..."},
        "fundamentalContext": {"valuation": "...", "analystSentiment": "...", "insiderActivity": "...", "peerPerformance": "..."},
        "behavioralSentiment": {"buyerVsSeller": "...", "emotionalTone": "...", "newsReaction": "..."},
        "openingTradePlan": {"planName": "...", "knownParticipant": "...", "expectedParticipant": "...", "trigger": "...", "invalidation": "..."},
        "alternativePlan": {"planName": "...", "scenario": "...", "knownParticipant": "...", "expectedParticipant": "...", "trigger": "...", "invalidation": "..."},
        "todaysAction": "Action 1"
    }
    mock_resp2 = mock_resp1.copy()
    mock_resp2["todaysAction"] = "Action 2 (Overwrite)"
    
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

        # The immutability fix preserves the FIRST entry written for a given date.
        # A second call with the same trade date must NOT overwrite the original.
        date_entries = [e for e in log if e['date'] == today.isoformat()]
        assert len(date_entries) == 1
        assert date_entries[0]['action'] == "Action 1", (
            "Immutability violation: the original keyActionLog entry was overwritten."
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
