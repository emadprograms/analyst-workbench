
import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, patch
from datetime import date, datetime
from modules.data.data_processing import (
    fetch_intraday_data,
    calculate_vwap,
    calculate_volume_profile,
    calculate_opening_range,
    find_key_volume_events,
    get_vwap_interaction,
    generate_analysis_text,
    parse_raw_summary,
    split_stock_summaries
)

# --- TEST DATA ---

@pytest.fixture
def sample_intraday_df():
    """Creates a sample intraday DataFrame for testing."""
    dates = pd.date_range(start="2023-10-27 09:30", end="2023-10-27 16:00", freq="5min")
    prices = np.linspace(100, 110, len(dates)//2).tolist() + np.linspace(110, 100, len(dates) - len(dates)//2).tolist()

    df = pd.DataFrame({
        'Datetime': dates,
        'Open': prices,
        'High': [p + 0.5 for p in prices],
        'Low': [p - 0.5 for p in prices],
        'Close': prices,
        'Volume': [1000] * len(dates),
        'Ticker': 'TEST'
    })
    return df

@pytest.fixture
def empty_df():
    return pd.DataFrame()

# --- FETCH TESTS (SUCCESS) ---

# --- FETCH TESTS (DB MOCK) ---

@patch('modules.data.data_processing.get_db_connection')
def test_fetch_intraday_data_success(mock_get_db, sample_intraday_df):
    """Test successful data fetching via DB."""
    # Setup Mock DB
    mock_conn = MagicMock()
    mock_get_db.return_value = mock_conn
    
    # Mock Rows logic
    # The function expects rows as tuples/lists: (symbol, timestamp, open, high, low, close, volume)
    # The sample_df has these columns.
    
    rows = []
    # We need to match the SQL select order: symbol, timestamp, open, high, low, close, volume
    # sample_intraday_df has 'Ticker' as symbol.
    
    for idx, row in sample_intraday_df.iterrows():
        # Timestamp in DB is likely string or datetime. Code converts `row[1]` to datetime.
        # Let's provide strings as DBs usually return strings/objects.
        ts_str = row['Datetime'].strftime('%Y-%m-%d %H:%M:%S')
        rows.append((
            'TEST', 
            ts_str, 
            row['Open'], 
            row['High'], 
            row['Low'], 
            row['Close'], 
            row['Volume']
        ))
        
    mock_rs = MagicMock()
    mock_rs.rows = rows
    mock_conn.execute.return_value = mock_rs
    
    # Call function
    result = fetch_intraday_data(['TEST'], date(2023, 10, 27))
    
    assert not result.empty
    assert 'Ticker' in result.columns
    assert len(result) == len(sample_intraday_df)
    assert result.iloc[0]['Ticker'] == 'TEST'

@patch('modules.data.data_processing.get_db_connection')
def test_fetch_intraday_data_empty_db(mock_get_db):
    """Test when DB returns no rows."""
    mock_conn = MagicMock()
    mock_get_db.return_value = mock_conn
    mock_rs = MagicMock()
    mock_rs.rows = []
    mock_conn.execute.return_value = mock_rs
    
    result = fetch_intraday_data(['TEST'], date(2023, 10, 27))
    assert result.empty

@patch('modules.data.data_processing.get_db_connection')
def test_fetch_intraday_data_conn_failure(mock_get_db):
    """Test when DB connection fails."""
    mock_get_db.return_value = None # Connection failed
    
    result = fetch_intraday_data(['TEST'], date(2023, 10, 27))
    assert result.empty

# --- GENERATION TESTS (FAILURES) ---

def test_generate_analysis_text_none_input():
    """Test with None input."""
    result = generate_analysis_text(None, date(2023, 10, 27))
    assert "[ERROR]" in result

def test_generate_analysis_text_empty_list():
    """Test with empty list."""
    result = generate_analysis_text([], date(2023, 10, 27))
    assert "[ERROR]" in result

@patch('modules.data.data_processing.fetch_intraday_data')
def test_generate_analysis_text_fetch_empty(mock_fetch):
    """Test when fetch returns empty dataframe."""
    mock_fetch.return_value = pd.DataFrame()
    result = generate_analysis_text(['TEST'], date(2023, 10, 27))
    assert "[ERROR] No data found" in result

# --- ANALYSIS TESTS ---

def test_calculate_vwap(sample_intraday_df):
    vwap = calculate_vwap(sample_intraday_df)
    assert len(vwap) == len(sample_intraday_df)

def test_calculate_volume_profile(sample_intraday_df):
    poc, vah, val = calculate_volume_profile(sample_intraday_df)
    assert not np.isnan(poc)

def test_calculate_opening_range(sample_intraday_df):
    orh, orl, narrative = calculate_opening_range(sample_intraday_df)
    assert isinstance(orh, float)

def test_find_key_volume_events(sample_intraday_df):
    events = find_key_volume_events(sample_intraday_df)
    assert len(events) > 0

def test_get_vwap_interaction(sample_intraday_df):
    vwap = calculate_vwap(sample_intraday_df)
    interaction = get_vwap_interaction(sample_intraday_df, vwap)
    assert isinstance(interaction, str)

def test_parse_raw_summary():
    raw_text = """
Data Extraction Summary: AAPL | 2023-10-27
==================================================
1. Session Extremes & Timing:
   - Open: $170.00
   - Close: $175.00
   - High of Day (HOD): $176.00 (Set at 14:00)
   - Low of Day (LOD): $169.00 (Set at 09:30)
2. Volume Profile (Value References):
   - Point of Control (POC): $172.00 (Highest volume traded)
   - Value Area High (VAH): $174.00
   - Value Area Low (VAL): $171.00
3. Key Intraday Volume Events:
   - 10:00 @ $171.00 (Vol: 1,000) - [Neutral Bar]
4. VWAP Relationship:
   - Session VWAP: $173.00
   - Close vs. VWAP: Above
   - Key Interactions: VWAP primarily acted as Support.
5. Opening Range Analysis (First 30 Mins):
   - Opening Range: $169.50 - $170.50
   - Outcome Narrative: Price broke above ORH at 10:00.
"""
    parsed = parse_raw_summary(raw_text)
    assert parsed['ticker'] == 'AAPL'
    assert parsed['vwap_narrative'] == 'Support'

def test_split_stock_summaries():
    raw_text = "Data Extraction Summary: AAPL | 2023-10-27\n... content ...\nData Extraction Summary: TSLA | 2023-10-27\n... content ..."
    splits = split_stock_summaries(raw_text)
    assert 'AAPL' in splits
    assert 'TSLA' in splits
