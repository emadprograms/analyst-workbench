import pytest
from unittest.mock import MagicMock, patch
from datetime import date
from modules.data.db_utils import (
    upsert_daily_inputs,
    get_daily_inputs,
    get_latest_daily_input_date,
    get_economy_card,
    get_archived_economy_card,
    get_all_tickers_from_db,
    get_company_card_and_notes,
    get_all_archive_dates,
    get_all_tickers_for_archive_date,
    get_archived_company_card,
    get_db_connection,
    upsert_data_archive,
    get_data_archive
)

# --- MOCK DB CLIENT ---

@pytest.fixture
def mock_db_client():
    with patch('modules.data.db_utils.get_db_connection') as mock_conn:
        client = MagicMock()
        mock_conn.return_value = client
        yield client

# --- TESTS ---

def test_upsert_daily_inputs(mock_db_client):
    mock_db_client.execute.return_value = None
    result = upsert_daily_inputs(date(2023, 10, 27), "News Summary")
    assert result is True
    mock_db_client.execute.assert_called_once()
    sql = mock_db_client.execute.call_args[0][0]
    assert "INSERT INTO daily_inputs" in sql
    assert "(target_date, news_text)" in sql

def test_get_daily_inputs(mock_db_client):
    # Mock result set
    mock_row = {'news_text': 'Some news'}
    mock_rs = MagicMock()
    mock_rs.rows = [mock_row]
    mock_db_client.execute.return_value = mock_rs

    news, summaries = get_daily_inputs(date(2023, 10, 27))

    assert news == 'Some news'
    assert summaries is None

def test_get_latest_daily_input_date(mock_db_client):
    mock_row = {'target_date': '2023-10-27'}
    mock_rs = MagicMock()
    mock_rs.rows = [mock_row]
    mock_db_client.execute.return_value = mock_rs

    latest_date = get_latest_daily_input_date()
    assert latest_date == '2023-10-27'

def test_get_economy_card_default(mock_db_client):
    # Test returning default if no rows
    mock_rs = MagicMock()
    mock_rs.rows = []
    mock_db_client.execute.return_value = mock_rs

    card, dt = get_economy_card()
    assert "marketNarrative" in card
    assert dt is None

def test_get_all_tickers_from_db(mock_db_client):
    mock_rows = [{'ticker': 'AAPL'}, {'ticker': 'MSFT'}]
    mock_rs = MagicMock()
    mock_rs.rows = mock_rows
    mock_db_client.execute.return_value = mock_rs

    tickers = get_all_tickers_from_db()
    assert tickers == ['AAPL', 'MSFT']

def test_get_company_card_and_notes(mock_db_client):
    # Call 1: Notes
    mock_rs_notes = MagicMock()
    mock_rs_notes.rows = [{'historical_level_notes': 'Major Support: 100'}]

    # Call 2: Card
    mock_rs_card = MagicMock()
    mock_rs_card.rows = [{'company_card_json': '{"ticker": "AAPL"}', 'date': '2023-10-26'}]

    mock_db_client.execute.side_effect = [mock_rs_notes, mock_rs_card]

    card, notes, dt = get_company_card_and_notes('AAPL')

    assert 'AAPL' in card
    assert notes == 'Major Support: 100'
    assert dt == '2023-10-26'

def test_get_all_archive_dates(mock_db_client):
    mock_rs = MagicMock()
    mock_rs.rows = [{'date': '2023-10-27'}, {'date': '2023-10-26'}]
    mock_db_client.execute.return_value = mock_rs

    dates = get_all_archive_dates()
    assert dates == ['2023-10-27', '2023-10-26']

def test_get_archived_company_card(mock_db_client):
    mock_rs = MagicMock()
    mock_rs.rows = [{'company_card_json': '{}', 'raw_text_summary': 'Raw Data'}]
    mock_db_client.execute.return_value = mock_rs

    card, raw = get_archived_company_card(date(2023, 10, 27), 'AAPL')
    assert card == '{}'
    assert raw == 'Raw Data'

# --- FAILURE TESTS (DB DOWN) ---

@patch('modules.data.db_utils.get_db_connection')
def test_db_connection_failed(mock_conn):
    """Test behavior when DB connection returns None."""
    mock_conn.return_value = None

    # 1. Upsert
    result = upsert_daily_inputs(date(2023, 10, 27), "News")
    assert result is False # Should return False, not crash

    # 2. Get Daily Inputs
    news, summaries = get_daily_inputs(date(2023, 10, 27))
    assert news is None
    assert summaries is None

    # 3. Get Economy Card
    card, dt = get_economy_card()
    assert "marketNarrative" in card # Returns default
    assert dt is None

    # 4. Get Company Card
    card, notes, dt = get_company_card_and_notes("AAPL")
    assert "AAPL" in card # Returns default
    assert notes == ""
    assert dt is None

    # 5. Get Tickers
    tickers = get_all_tickers_from_db()
    assert tickers == []

def test_upsert_data_archive(mock_db_client):
    mock_db_client.execute.return_value = None
    result = upsert_data_archive(date(2023, 10, 27), "AAPL", "Image Summary")
    assert result is True
    mock_db_client.execute.assert_called_once()
    sql = mock_db_client.execute.call_args[0][0]
    assert "INSERT INTO data_archive" in sql

def test_get_data_archive(mock_db_client):
    mock_row = {'raw_text_summary': 'Archived Content'}
    mock_rs = MagicMock()
    mock_rs.rows = [mock_row]
    mock_db_client.execute.return_value = mock_rs

    content = get_data_archive(date(2023, 10, 27), "AAPL")
    assert content == 'Archived Content'
