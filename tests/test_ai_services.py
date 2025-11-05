import pytest
import sqlite3
import os
import json
from unittest.mock import MagicMock, patch

# Add parent directory to path to allow module imports
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.ai_services import update_stock_note, update_economy_card
from modules.config import DATABASE_FILE, DEFAULT_COMPANY_OVERVIEW_JSON, DEFAULT_ECONOMY_CARD_JSON

# --- Test Setup and Teardown ---

@pytest.fixture(scope="function")
def mock_db():
    """
    Creates an in-memory SQLite database for testing and populates it with initial data.
    Yields the connection object and then cleans up.
    """
    # Use a temporary file for the database
    db_file = "test_analysis_database.db"
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    # Create tables from the actual setup script
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS stocks (
        ticker TEXT PRIMARY KEY,
        historical_level_notes TEXT,
        company_overview_card_json TEXT,
        last_updated DATE
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS data_archive (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT,
        date DATE,
        raw_text_summary TEXT,
        open REAL, high REAL, low REAL, close REAL,
        poc REAL, vah REAL, val REAL, vwap REAL,
        orl REAL, orh REAL,
        UNIQUE(ticker, date)
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS market_context (
        context_id INTEGER PRIMARY KEY,
        economy_card_json TEXT,
        last_updated DATE
    );
    """)
    
    # Insert sample data
    cursor.execute("INSERT INTO stocks (ticker, historical_level_notes, company_overview_card_json) VALUES (?, ?, ?)",
                   ('TEST', 'Major Support at 100', DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", "TEST")))
    cursor.execute("INSERT INTO market_context (context_id, economy_card_json) VALUES (?, ?)",
                   (1, DEFAULT_ECONOMY_CARD_JSON))
    conn.commit()

    # Patch the DATABASE_FILE constant to use the test DB
    with patch('modules.ai_services.DATABASE_FILE', db_file):
        yield conn

    # Teardown: close connection and remove the temp file
    conn.close()
    if os.path.exists(db_file):
        os.remove(db_file)

# --- Mock Objects ---

class MockLogger:
    """A mock logger to capture log messages."""
    def __init__(self):
        self.logs = []
    def log(self, message):
        self.logs.append(str(message))
    def log_code(self, data, language='json'):
        self.logs.append(str(data))

@patch('modules.ai_services.call_gemini_api')
def test_update_stock_note_success(mock_call_gemini_api, mock_db):
    """
    Tests the successful update of a stock note.
    """
    # --- Arrange ---
    mock_logger = MockLogger()
    ticker = 'TEST'
    raw_text = "Summary: TEST | 2023-10-27\n- Close: 105.00"
    macro_context = "Market was bullish."
    
    # Mock the Gemini API response
    ai_response = json.loads(DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", "TEST"))
    ai_response['confidence'] = "High - Test Update"
    ai_response['openingTradePlan']['planName'] = "Updated by Test"
    mock_call_gemini_api.return_value = json.dumps(ai_response)

    # --- Act ---
    update_stock_note(ticker, raw_text, macro_context, 'fake_api_key', mock_logger, db_connection=mock_db)

    # --- Assert ---
    # Check logs
    logs_str = "".join(mock_logger.logs)
    assert f"--- Starting EOD update for {ticker} ---" in logs_str
    assert "2. Archiving raw data..." in logs_str
    assert "5. Calling EOD AI Analyst..." in logs_str
    assert "7. Saving NEW EOD Card..." in logs_str
    assert f"--- Success EOD update {ticker} ---" in logs_str

    # Check database state
    cursor = mock_db.cursor()
    cursor.execute("SELECT company_overview_card_json FROM stocks WHERE ticker=?", (ticker,))
    new_card_str = cursor.fetchone()[0]
    new_card = json.loads(new_card_str)
    
    assert new_card['confidence'] == "High - Test Update"
    assert new_card['openingTradePlan']['planName'] == "Updated by Test"

    # Check archive table
    cursor.execute("SELECT close FROM data_archive WHERE ticker=?", (ticker,))
    archived_close = cursor.fetchone()[0]
    assert archived_close == 105.00

@patch('modules.ai_services.call_gemini_api')
def test_update_economy_card_success(mock_call_gemini_api, mock_db):
    """
    Tests the successful update of the economy card.
    """
    # --- Arrange ---
    mock_logger = MockLogger()
    manual_summary = "User thinks the market is strong."
    etf_summaries = "SPY closed up."

    # Mock the Gemini API response
    ai_response = json.loads(DEFAULT_ECONOMY_CARD_JSON)
    ai_response['marketNarrative'] = "AI confirms market is strong."
    ai_response['marketBias'] = "Bullish"
    mock_call_gemini_api.return_value = json.dumps(ai_response)

    # --- Act ---
    update_economy_card(manual_summary, etf_summaries, 'fake_api_key', mock_logger, db_connection=mock_db)

    # --- Assert ---
    # Check logs
    logs_str = "".join(mock_logger.logs)
    assert "--- Starting Economy Card EOD Update ---" in logs_str
    assert "3. Calling Macro Strategist AI..." in logs_str
    assert "5. Saving new Economy Card to database..." in logs_str
    assert "--- Success: Economy Card EOD update complete! ---" in logs_str

    # Check database state
    cursor = mock_db.cursor()
    cursor.execute("SELECT economy_card_json FROM market_context WHERE context_id=1")
    new_card_str = cursor.fetchone()[0]
    new_card = json.loads(new_card_str)

    assert new_card['marketNarrative'] == "AI confirms market is strong."
    assert new_card['marketBias'] == "Bullish"

def test_update_stock_note_no_ai_response(mock_db):
    """
    Tests that the function handles a failure from the AI API gracefully.
    """
    with patch('modules.ai_services.call_gemini_api', return_value=None) as mock_api:
        mock_logger = MockLogger()
        update_stock_note('TEST', 'Summary: TEST | 2023-10-27', '', 'key', mock_logger, db_connection=mock_db)
        
        logs_str = "".join(mock_logger.logs)
        assert "Error: No AI response." in logs_str
        assert "Success" not in logs_str

        # Verify DB was not updated
        cursor = mock_db.cursor()
        cursor.execute("SELECT company_overview_card_json FROM stocks WHERE ticker='TEST'")
        card_str = cursor.fetchone()[0]
        original_card = json.loads(DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", "TEST"))
        db_card = json.loads(card_str)
        assert db_card['confidence'] == original_card['confidence']

@patch('modules.ai_services.call_gemini_api')
def test_update_stock_note_invalid_json_from_ai(mock_call_gemini_api, mock_db):
    """
    Tests that the system handles a non-JSON response from the AI gracefully.
    """
    # --- Arrange ---
    mock_logger = MockLogger()
    ticker = 'TEST'
    raw_text = "Summary: TEST | 2023-10-27\\n- Close: 105.00"
    macro_context = "Market was bullish."

    # Mock the Gemini API to return a non-JSON string
    mock_call_gemini_api.return_value = "This is not valid JSON."

    # Get the original state of the card from the DB
    cursor = mock_db.cursor()
    cursor.execute("SELECT company_overview_card_json FROM stocks WHERE ticker=?", (ticker,))
    original_card_str = cursor.fetchone()[0]

    # --- Act ---
    update_stock_note(ticker, raw_text, macro_context, 'fake_api_key', mock_logger, db_connection=mock_db)

    # --- Assert ---
    # Check that the error was logged
    logs_str = "".join(mock_logger.logs)
    assert "Error: Failed to decode AI response JSON" in logs_str
    assert "Success" not in logs_str

    # Check that the database was NOT updated
    cursor.execute("SELECT company_overview_card_json FROM stocks WHERE ticker=?", (ticker,))
    final_card_str = cursor.fetchone()[0]
    assert final_card_str == original_card_str
