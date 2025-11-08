import sys
import os

# Add the project root to the Python path to allow for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
import sqlite3
from unittest.mock import patch, MagicMock
from datetime import date
import pandas as pd

# --- Local Imports ---
from modules.db_utils import upsert_daily_inputs, get_all_tickers_from_db
from modules.data_processing import generate_analysis_text
from modules.setup_db import setup_database

@pytest.fixture
def test_db():
    """Fixture to set up and tear down an in-memory SQLite database for testing."""
    conn = sqlite3.connect(":memory:")
    # Create the schema from setup_db.py
    setup_database(conn)
    
    # Add some dummy data
    cursor = conn.cursor()
    cursor.execute("INSERT INTO stocks (ticker) VALUES ('AAPL'), ('GOOG')")
    conn.commit()

    yield conn
    conn.close()

@patch('modules.db_utils.DATABASE_FILE', ':memory:')
def test_upsert_daily_inputs(test_db):
    """
    Tests the upsert functionality for daily inputs.
    - It should insert a new record.
    - It should update an existing record on conflict.
    """
    # Use the connection from the fixture
    with patch('modules.db_utils.get_db_connection') as mock_get_conn:
        mock_get_conn.return_value = test_db
        
        test_date = date(2025, 11, 5)
        market_news_1 = "Market was up today."
        etf_summaries_1 = "SPY and QQQ were green."

        # 1. Test Insert
        result_insert = upsert_daily_inputs(test_date, market_news_1, etf_summaries_1)
        assert result_insert is True

        cursor = test_db.cursor()
        cursor.execute("SELECT market_news, combined_etf_summaries FROM daily_inputs WHERE date = ?", (test_date.isoformat(),))
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == market_news_1
        assert row[1] == etf_summaries_1

        # 2. Test Update (Upsert)
        market_news_2 = "Market was actually down."
        etf_summaries_2 = "SPY and QQQ were red."
        result_update = upsert_daily_inputs(test_date, market_news_2, etf_summaries_2)
        assert result_update is True

        cursor.execute("SELECT market_news, combined_etf_summaries FROM daily_inputs WHERE date = ?", (test_date.isoformat(),))
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == market_news_2
        assert row[1] == etf_summaries_2

@patch('modules.db_utils.DATABASE_FILE', ':memory:')
def test_get_all_tickers_from_db(test_db):
    """Tests that the function retrieves all unique tickers correctly."""
    with patch('modules.db_utils.get_db_connection') as mock_get_conn:
        mock_get_conn.return_value = test_db
        tickers = get_all_tickers_from_db()
        assert tickers == ['AAPL', 'GOOG']

@patch('modules.data_processing.get_gemini_model')
def test_generate_analysis_text(mock_get_gemini_model):
    """
    Tests the analysis text generation by mocking the AI model.
    Ensures the function formats the output as expected.
    """
    # Mock the Gemini model and its response
    mock_model_instance = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "This is a test summary for AAPL."
    mock_model_instance.generate_content.return_value = mock_response
    mock_get_gemini_model.return_value = mock_model_instance

    tickers = ["AAPL", "GOOG"]
    test_date = date(2025, 11, 5)
    
    result = generate_analysis_text(tickers, test_date)

    # Verify the model was called for each ticker
    assert mock_model_instance.generate_content.call_count == len(tickers)
    
    # Verify the output format
    assert "--- TICKER: AAPL ---" in result
    assert "This is a test summary for AAPL." in result
    assert "--- TICKER: GOOG ---" in result
