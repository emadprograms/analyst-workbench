import os
import json
import shutil
import pandas as pd
from unittest.mock import MagicMock
from modules.analysis.impact_engine import get_or_compute_context
from modules.core.logger import AppLogger

def test_cache_staleness():
    logger = AppLogger()
    ticker = "TEST"
    date_str = "2024-01-01"
    cache_dir = "cache/context"
    cache_file = f"{cache_dir}/{ticker}_{date_str}.json"
    
    # Ensure clean state
    if os.path.exists(cache_file):
        os.remove(cache_file)
    
    # Mock DB response with PARTIAL data (e.g. Pre-market only)
    # We need to mock get_session_bars_from_db and get_previous_session_stats
    import modules.analysis.impact_engine as ie
    
    original_get_bars = ie.get_session_bars_from_db
    original_get_stats = ie.get_previous_session_stats
    
    try:
        # 1. First run: Partial data
        df1 = pd.DataFrame({
            'timestamp': pd.to_datetime(['2024-01-01 08:00:00', '2024-01-01 09:00:00'], utc=True),
            'Open': [100, 101],
            'High': [102, 103],
            'Low': [99, 100],
            'Close': [101, 102],
            'Volume': [1000, 1100]
        })
        # Simulate the 'dt_eastern' which the engine expects or creates
        df1['dt_eastern'] = df1['timestamp'].dt.tz_convert('US/Eastern')
        
        ie.get_session_bars_from_db = MagicMock(return_value=df1)
        ie.get_previous_session_stats = MagicMock(return_value={})
        
        print("--- Run 1: Partial Data ---")
        context1 = get_or_compute_context(None, ticker, date_str, logger)
        print(f"Data points in context 1: {context1['meta']['data_points']}")
        
        # 2. Second run: More data available in DB
        df2 = pd.concat([df1, pd.DataFrame({
            'timestamp': pd.to_datetime(['2024-01-01 10:00:00', '2024-01-01 11:00:00'], utc=True),
            'Open': [102, 103],
            'High': [104, 105],
            'Low': [101, 102],
            'Close': [103, 104],
            'Volume': [1200, 1300]
        })], ignore_index=True)
        df2['dt_eastern'] = df2['timestamp'].dt.tz_convert('US/Eastern')
        
        ie.get_session_bars_from_db = MagicMock(return_value=df2)
        
        print("\n--- Run 2: More Data Available in DB ---")
        context2 = get_or_compute_context(None, ticker, date_str, logger)
        print(f"Data points in context 2: {context2['meta']['data_points']}")
        
        if context2['meta']['data_points'] == context1['meta']['data_points']:
            print("\n❌ STALENESS CONFIRMED: Context was loaded from cache despite more data being available.")
        else:
            print("\n✅ CACHE REFRESHED: Context reflected new data.")

    finally:
        ie.get_session_bars_from_db = original_get_bars
        ie.get_previous_session_stats = original_get_stats
        if os.path.exists(cache_file):
            os.remove(cache_file)

if __name__ == "__main__":
    test_cache_staleness()
