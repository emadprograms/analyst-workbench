"""
Comprehensive tests for the Impact Engine (modules/analysis/impact_engine.py).

Tests cover:
- Volume column renaming (the critical bug fix)
- Session slicing (Pre-Market, RTH, Post-Market)
- Impact level detection (Support/Resistance)
- Value Migration analysis
- Volume Profile (POC, VAH, VAL)
- Key Volume Events
- Caching layer (get_or_compute_context)
- Edge cases: empty data, single row, missing columns
"""
import pytest
import pandas as pd
import numpy as np
import json
import os
import shutil
from datetime import datetime, timedelta, time as dt_time
from pytz import timezone as pytz_timezone
from unittest.mock import patch, MagicMock

# Must set env before importing modules that load config
os.environ["DISABLE_INFISICAL"] = "1"

from modules.analysis.impact_engine import (
    _detect_impact_levels,
    _analyze_slice_migration,
    _calculate_volume_profile,
    _find_key_volume_events,
    analyze_market_context,
    get_or_compute_context,
    get_latest_price_details,
    get_session_bars_from_db,
    get_previous_session_stats,
    US_EASTERN,
)


# ==========================================
# HELPERS: Build Realistic Test DataFrames
# ==========================================

def _make_bar(dt_utc, open_p, high, low, close, volume=1000):
    """Create a single price bar dict with UTC timestamp."""
    return {
        'timestamp': dt_utc,
        'Open': open_p,
        'High': high,
        'Low': low,
        'Close': close,
        'Volume': volume,
        'dt_eastern': dt_utc.astimezone(US_EASTERN),
    }


def _make_bars_df(bars: list[dict]) -> pd.DataFrame:
    """Convert list of bar dicts into a DataFrame matching impact engine expectations."""
    df = pd.DataFrame(bars)
    for col in ['Open', 'High', 'Low', 'Close']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.reset_index(drop=True)


def _build_day_df(base_date_str="2026-02-23", ticker_price=100.0, num_bars=78):
    """
    Build a full trading day DataFrame with realistic session distribution.
    Pre-market: 04:00-09:30 (11 bars @ 30min)
    RTH: 09:30-16:00 (13 bars @ 30min)
    Post-market: 16:00-20:00 (8 bars @ 30min)
    """
    utc = pytz_timezone('UTC')
    bars = []
    base = datetime.strptime(base_date_str, "%Y-%m-%d").replace(tzinfo=utc)
    
    # Pre-market: 09:00-13:30 UTC (04:00-08:30 ET)
    for i in range(11):
        t = base.replace(hour=9) + timedelta(minutes=30 * i)
        noise = np.random.uniform(-0.5, 0.5)
        o = ticker_price + noise
        h = o + abs(np.random.uniform(0.1, 0.8))
        l = o - abs(np.random.uniform(0.1, 0.8))
        c = np.random.uniform(l, h)
        bars.append(_make_bar(t, round(o,2), round(h,2), round(l,2), round(c,2), volume=int(np.random.uniform(500, 5000))))
    
    # RTH: 14:30-21:00 UTC (09:30-16:00 ET)
    for i in range(13):
        t = base.replace(hour=14, minute=30) + timedelta(minutes=30 * i)
        noise = np.random.uniform(-1, 1)
        o = ticker_price + noise
        h = o + abs(np.random.uniform(0.2, 1.5))
        l = o - abs(np.random.uniform(0.2, 1.5))
        c = np.random.uniform(l, h)
        bars.append(_make_bar(t, round(o,2), round(h,2), round(l,2), round(c,2), volume=int(np.random.uniform(10000, 100000))))
    
    # Post-market: 21:00-01:00 UTC (16:00-20:00 ET)
    for i in range(8):
        t = base.replace(hour=21) + timedelta(minutes=30 * i)
        noise = np.random.uniform(-0.3, 0.3)
        o = ticker_price + noise
        h = o + abs(np.random.uniform(0.05, 0.3))
        l = o - abs(np.random.uniform(0.05, 0.3))
        c = np.random.uniform(l, h)
        bars.append(_make_bar(t, round(o,2), round(h,2), round(l,2), round(c,2), volume=int(np.random.uniform(100, 2000))))
    
    return _make_bars_df(bars)


# ==========================================
# TEST: Volume Column Naming (Critical Bug Fix)
# ==========================================

class TestVolumeColumnFix:
    """Verify that the Volume column is correctly capitalized after DB fetch."""

    @patch('modules.analysis.impact_engine.get_price_db_connection')
    def test_get_session_bars_renames_volume_column(self, mock_conn_fn):
        """The critical fix: volume must be renamed to Volume."""
        mock_conn = MagicMock()
        mock_conn_fn.return_value = mock_conn
        
        # Simulate DB result with lowercase column names
        mock_rs = MagicMock()
        mock_rs.rows = [
            ('2026-02-23 14:30:00', 100.0, 101.0, 99.0, 100.5, 50000, 'RTH'),
            ('2026-02-23 15:00:00', 100.5, 102.0, 100.0, 101.5, 60000, 'RTH'),
        ]
        mock_conn.execute.return_value = mock_rs
        
        from modules.core.logger import AppLogger
        logger = AppLogger("test")
        
        df = get_session_bars_from_db(None, "SPY", "2026-02-23", "2026-02-23 23:59:59", logger)
        
        assert df is not None
        assert 'Volume' in df.columns, "Volume column must be capitalized (was lowercase 'volume' before fix)"
        assert 'volume' not in df.columns, "Lowercase 'volume' should not exist after rename"
        assert df['Volume'].sum() == 110000

    def test_volume_profile_with_correct_column(self):
        """Volume profile should work when column is properly named."""
        utc = pytz_timezone('UTC')
        base = datetime(2026, 2, 23, 15, 0, tzinfo=utc)
        bars = []
        for i in range(20):
            t = base + timedelta(minutes=i)
            bars.append(_make_bar(t, 100.0 + i*0.1, 100.5 + i*0.1, 99.5 + i*0.1, 100.2 + i*0.1, volume=10000))
        df = _make_bars_df(bars)
        
        poc, vah, val = _calculate_volume_profile(df)
        assert poc is not None, "POC should be computed when Volume column exists"
        assert vah is not None, "VAH should be computed"
        assert val is not None, "VAL should be computed"

    def test_volume_profile_returns_none_when_no_volume(self):
        """Volume profile should gracefully return None when Volume column missing."""
        df = pd.DataFrame({
            'Open': [100], 'High': [101], 'Low': [99], 'Close': [100.5]
        })
        poc, vah, val = _calculate_volume_profile(df)
        assert poc is None
        assert vah is None
        assert val is None

    def test_volume_profile_returns_none_when_volume_zero(self):
        """Volume profile should handle all-zero volume."""
        df = pd.DataFrame({
            'Open': [100, 101], 'High': [101, 102], 'Low': [99, 100],
            'Close': [100.5, 101.5], 'Volume': [0, 0]
        })
        poc, vah, val = _calculate_volume_profile(df)
        assert poc is None


# ==========================================
# TEST: Session Slicing
# ==========================================

class TestSessionSlicing:
    """Verify that analyze_market_context correctly splits data into 3 sessions."""

    def test_three_sessions_present(self):
        df = _build_day_df()
        result = analyze_market_context(df, {}, ticker="TEST")
        
        assert "sessions" in result
        assert "pre_market" in result["sessions"]
        assert "regular_hours" in result["sessions"]
        assert "post_market" in result["sessions"]

    def test_pre_market_has_lower_volume(self):
        """Pre-market volume should generally be lower than RTH."""
        df = _build_day_df()
        result = analyze_market_context(df, {}, ticker="TEST")
        
        pre_vol = result["sessions"]["pre_market"].get("volume_approx", 0)
        rth_vol = result["sessions"]["regular_hours"].get("volume_approx", 0)
        
        # Pre-market bars have volume 500-5000, RTH has 10000-100000
        assert rth_vol > pre_vol, "RTH volume should exceed pre-market"

    def test_empty_session_returns_no_data(self):
        """If only RTH data exists, pre/post should be 'No Data'."""
        utc = pytz_timezone('UTC')
        base = datetime(2026, 2, 23, 14, 30, tzinfo=utc)  # 09:30 ET
        bars = []
        for i in range(13):
            t = base + timedelta(minutes=30 * i)
            bars.append(_make_bar(t, 100, 101, 99, 100.5, 50000))
        df = _make_bars_df(bars)
        
        result = analyze_market_context(df, {}, ticker="TEST")
        assert result["sessions"]["pre_market"]["status"] == "No Data"
        assert result["sessions"]["regular_hours"]["status"] == "Active"
        assert result["sessions"]["post_market"]["status"] == "No Data"


# ==========================================
# TEST: Impact Level Detection
# ==========================================

class TestImpactLevels:
    
    def test_detects_resistance(self):
        """A clear peak followed by a decline should be detected as resistance."""
        utc = pytz_timezone('UTC')
        base = datetime(2026, 2, 23, 15, 0, tzinfo=utc)
        bars = []
        # Rise to 105, then fall
        prices = [100, 101, 102, 103, 104, 105, 104, 103, 102, 101, 100, 99]
        for i, p in enumerate(prices):
            t = base + timedelta(minutes=5 * i)
            bars.append(_make_bar(t, p, p + 0.5, p - 0.5, p, 10000))
        df = _make_bars_df(bars)
        
        levels = _detect_impact_levels(df, avg_price=102.0)
        resistance = [l for l in levels if l['type'] == 'RESISTANCE']
        assert len(resistance) > 0, "Should detect at least one resistance level"
        assert any(abs(r['level'] - 105.5) < 1.0 for r in resistance), "Resistance should be near 105.5"

    def test_detects_support(self):
        """A clear valley followed by a bounce should be detected as support."""
        utc = pytz_timezone('UTC')
        base = datetime(2026, 2, 23, 15, 0, tzinfo=utc)
        bars = []
        # Fall to 95, then bounce
        prices = [100, 99, 98, 97, 96, 95, 96, 97, 98, 99, 100, 101]
        for i, p in enumerate(prices):
            t = base + timedelta(minutes=5 * i)
            bars.append(_make_bar(t, p, p + 0.5, p - 0.5, p, 10000))
        df = _make_bars_df(bars)
        
        levels = _detect_impact_levels(df, avg_price=98.0)
        support = [l for l in levels if l['type'] == 'SUPPORT']
        assert len(support) > 0, "Should detect at least one support level"

    def test_no_levels_in_flat_data(self):
        """Completely flat price data should produce minimal or no levels."""
        utc = pytz_timezone('UTC')
        base = datetime(2026, 2, 23, 15, 0, tzinfo=utc)
        bars = []
        for i in range(20):
            t = base + timedelta(minutes=5 * i)
            bars.append(_make_bar(t, 100, 100.01, 99.99, 100, 10000))
        df = _make_bars_df(bars)
        
        levels = _detect_impact_levels(df, avg_price=100.0)
        # With essentially flat data, levels should be empty or insignificant
        for level in levels:
            assert level['magnitude'] < 0.1, "Flat data should not produce significant levels"

    def test_empty_dataframe(self):
        """Empty DataFrame should return empty list."""
        df = pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume', 'timestamp'])
        levels = _detect_impact_levels(df, avg_price=100.0)
        assert levels == []

    def test_single_row(self):
        """Single row should return empty (need at least 3 for shift detection)."""
        utc = pytz_timezone('UTC')
        df = _make_bars_df([_make_bar(
            datetime(2026, 2, 23, 15, 0, tzinfo=utc), 100, 101, 99, 100.5, 10000
        )])
        levels = _detect_impact_levels(df, avg_price=100.0)
        assert levels == []

    def test_two_rows(self):
        """Two rows: not enough for peak/valley detection with shift."""
        utc = pytz_timezone('UTC')
        bars = [
            _make_bar(datetime(2026, 2, 23, 15, 0, tzinfo=utc), 100, 102, 99, 101, 10000),
            _make_bar(datetime(2026, 2, 23, 15, 5, tzinfo=utc), 101, 103, 100, 102, 10000),
        ]
        df = _make_bars_df(bars)
        levels = _detect_impact_levels(df, avg_price=101.0)
        assert isinstance(levels, list)


# ==========================================
# TEST: Value Migration Analysis
# ==========================================

class TestValueMigration:
    
    def test_migration_log_has_entries(self):
        """Migration log should have entries for a multi-bar session."""
        utc = pytz_timezone('UTC')
        base = datetime(2026, 2, 23, 14, 30, tzinfo=utc)
        bars = []
        for i in range(60):  # 60 bars across 5 hours
            t = base + timedelta(minutes=5 * i)
            p = 100 + np.sin(i / 10) * 2
            bars.append(_make_bar(t, round(p, 2), round(p + 0.3, 2), round(p - 0.3, 2), round(p + 0.1, 2), 10000))
        df = _make_bars_df(bars)
        
        migration = _analyze_slice_migration(df)
        assert len(migration) > 0, "Should produce migration log entries"
        
        for entry in migration:
            assert "time" in entry
            assert "POC" in entry
            assert "nature" in entry
            assert "range" in entry

    def test_migration_empty_df(self):
        """Empty DataFrame should return empty list."""
        df = pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume', 'timestamp'])
        migration = _analyze_slice_migration(df)
        assert migration == []

    def test_migration_nature_labels(self):
        """Nature should contain Green/Red/Flat and range description."""
        utc = pytz_timezone('UTC')
        base = datetime(2026, 2, 23, 14, 30, tzinfo=utc)
        # Create clear upward movement
        bars = []
        for i in range(10):
            t = base + timedelta(minutes=3 * i)
            o = 100 + i
            bars.append(_make_bar(t, o, o + 1, o - 0.5, o + 0.5, 10000))
        df = _make_bars_df(bars)
        
        migration = _analyze_slice_migration(df)
        natures = [e['nature'] for e in migration]
        # Should have Green bars (close > open for upward movement)
        assert any('Green' in n for n in natures), "Upward movement should produce Green blocks"


# ==========================================
# TEST: Key Volume Events
# ==========================================

class TestKeyVolumeEvents:
    
    def test_finds_top_volume_bars(self):
        """Should return the top N bars by volume."""
        utc = pytz_timezone('UTC')
        base = datetime(2026, 2, 23, 14, 30, tzinfo=utc)
        bars = []
        for i in range(20):
            t = base + timedelta(minutes=5 * i)
            vol = 10000 if i != 10 else 500000  # Spike at bar 10
            bars.append(_make_bar(t, 100, 101, 99, 100.5, vol))
        df = _make_bars_df(bars)
        
        events = _find_key_volume_events(df, count=3)
        assert len(events) == 3
        assert events[0]['volume'] == 500000, "Highest volume event should be first"

    def test_events_contain_required_fields(self):
        utc = pytz_timezone('UTC')
        bars = [
            _make_bar(datetime(2026, 2, 23, 15, 0, tzinfo=utc), 100, 102, 99, 101, 50000),
            _make_bar(datetime(2026, 2, 23, 15, 5, tzinfo=utc), 101, 103, 100, 102, 60000),
            _make_bar(datetime(2026, 2, 23, 15, 10, tzinfo=utc), 102, 104, 101, 103, 70000),
        ]
        df = _make_bars_df(bars)
        
        events = _find_key_volume_events(df, count=2)
        for ev in events:
            assert 'time' in ev
            assert 'price' in ev
            assert 'volume' in ev
            assert 'action' in ev

    def test_events_detect_hod_and_lod(self):
        """Events at High-of-Day or Low-of-Day should be annotated."""
        utc = pytz_timezone('UTC')
        bars = [
            _make_bar(datetime(2026, 2, 23, 15, 0, tzinfo=utc), 100, 100.5, 95, 96, 100000),  # Sets LOD
            _make_bar(datetime(2026, 2, 23, 15, 5, tzinfo=utc), 96, 110, 96, 109, 200000),  # Sets HOD
            _make_bar(datetime(2026, 2, 23, 15, 10, tzinfo=utc), 109, 109.5, 108, 109, 50000),
        ]
        df = _make_bars_df(bars)
        events = _find_key_volume_events(df, count=3)
        
        actions = [e['action'] for e in events]
        assert any('High-of-Day' in a for a in actions), "Should detect HOD"
        assert any('Low-of-Day' in a for a in actions), "Should detect LOD"

    def test_empty_or_missing_columns(self):
        """Should return empty list for missing columns."""
        df = pd.DataFrame({'Open': [100], 'High': [101], 'Low': [99], 'Close': [100.5]})
        assert _find_key_volume_events(df) == []
        
        df_empty = pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume', 'dt_eastern'])
        assert _find_key_volume_events(df_empty) == []


# ==========================================
# TEST: Full Context Card (analyze_market_context)
# ==========================================

class TestAnalyzeMarketContext:
    
    def test_full_context_card_structure(self):
        """Verify the complete context card has all required sections."""
        df = _build_day_df()
        ref = {"yesterday_close": 99.5, "yesterday_high": 101, "yesterday_low": 98}
        
        result = analyze_market_context(df, ref, ticker="SPY")
        
        assert result["meta"]["ticker"] == "SPY"
        assert "data_points" in result["meta"]
        assert result["reference"] == ref
        assert "sessions" in result
        
        for session_name in ["pre_market", "regular_hours", "post_market"]:
            session = result["sessions"][session_name]
            if session["status"] == "Active":
                assert "high" in session
                assert "low" in session
                assert "volume_approx" in session
                assert "volume_profile" in session
                assert "key_levels" in session
                assert "value_migration" in session

    def test_none_dataframe(self):
        """None input should return a 'No Data' status."""
        result = analyze_market_context(None, {}, ticker="TEST")
        assert result["status"] == "No Data"

    def test_empty_dataframe(self):
        """Empty DataFrame should return 'No Data'."""
        df = pd.DataFrame(columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'dt_eastern'])
        result = analyze_market_context(df, {}, ticker="TEST")
        assert result["status"] == "No Data"


# ==========================================
# TEST: Caching (get_or_compute_context)
# ==========================================

class TestCaching:
    CACHE_DIR = "cache/context"
    TEST_TICKER = "TEST_CACHE"
    TEST_DATE = "2026-02-23"

    def setup_method(self):
        """Clean up test cache file before each test."""
        cache_file = f"{self.CACHE_DIR}/{self.TEST_TICKER}_{self.TEST_DATE}.json"
        if os.path.exists(cache_file):
            os.remove(cache_file)

    def teardown_method(self):
        """Clean up test cache file after each test."""
        cache_file = f"{self.CACHE_DIR}/{self.TEST_TICKER}_{self.TEST_DATE}.json"
        if os.path.exists(cache_file):
            os.remove(cache_file)

    @patch('modules.analysis.impact_engine.get_previous_session_stats')
    @patch('modules.analysis.impact_engine.get_session_bars_from_db')
    def test_cache_miss_computes_and_saves(self, mock_bars, mock_stats):
        """On cache miss, should compute context and save to file."""
        from modules.core.logger import AppLogger
        logger = AppLogger("test")
        
        # Return a minimal DataFrame
        utc = pytz_timezone('UTC')
        bars = [
            _make_bar(datetime(2026, 2, 23, 15, 0, tzinfo=utc), 100, 101, 99, 100.5, 10000),
            _make_bar(datetime(2026, 2, 23, 15, 5, tzinfo=utc), 100.5, 102, 100, 101, 10000),
            _make_bar(datetime(2026, 2, 23, 15, 10, tzinfo=utc), 101, 101.5, 100, 101, 10000),
        ]
        mock_bars.return_value = _make_bars_df(bars)
        mock_stats.return_value = {"yesterday_close": 99, "yesterday_high": 101, "yesterday_low": 98}
        
        result = get_or_compute_context(MagicMock(), self.TEST_TICKER, self.TEST_DATE, logger)
        
        assert result is not None
        assert result["meta"]["ticker"] == self.TEST_TICKER
        
        # Verify cache file was created
        cache_file = f"{self.CACHE_DIR}/{self.TEST_TICKER}_{self.TEST_DATE}.json"
        assert os.path.exists(cache_file)

    @patch('modules.analysis.impact_engine.get_previous_session_stats')
    @patch('modules.analysis.impact_engine.get_session_bars_from_db')
    def test_cache_hit_skips_compute(self, mock_bars, mock_stats):
        """On cache hit, should NOT call DB functions."""
        from modules.core.logger import AppLogger
        logger = AppLogger("test")
        
        # Pre-populate cache
        cache_file = f"{self.CACHE_DIR}/{self.TEST_TICKER}_{self.TEST_DATE}.json"
        os.makedirs(self.CACHE_DIR, exist_ok=True)
        cached_data = {"meta": {"ticker": self.TEST_TICKER}, "cached": True}
        with open(cache_file, "w") as f:
            json.dump(cached_data, f)
        
        result = get_or_compute_context(MagicMock(), self.TEST_TICKER, self.TEST_DATE, logger)
        
        assert result["cached"] is True
        mock_bars.assert_not_called()
        mock_stats.assert_not_called()


# ==========================================
# TEST: get_latest_price_details
# ==========================================

class TestGetLatestPriceDetails:
    
    @patch('modules.analysis.impact_engine.get_price_db_connection')
    def test_returns_price_and_timestamp(self, mock_conn_fn):
        mock_conn = MagicMock()
        mock_conn_fn.return_value = mock_conn
        mock_rs = MagicMock()
        mock_rs.rows = [(450.25, "2026-02-23 16:00:00")]
        mock_conn.execute.return_value = mock_rs
        
        from modules.core.logger import AppLogger
        price, ts = get_latest_price_details(None, "SPY", "2026-02-23 23:59:59", AppLogger("test"))
        
        assert price == 450.25
        assert ts == "2026-02-23 16:00:00"

    @patch('modules.analysis.impact_engine.get_price_db_connection')
    def test_returns_none_when_no_data(self, mock_conn_fn):
        mock_conn = MagicMock()
        mock_conn_fn.return_value = mock_conn
        mock_rs = MagicMock()
        mock_rs.rows = []
        mock_conn.execute.return_value = mock_rs
        
        from modules.core.logger import AppLogger
        price, ts = get_latest_price_details(None, "SPY", "2026-02-23 23:59:59", AppLogger("test"))
        
        assert price is None
        assert ts is None

    @patch('modules.analysis.impact_engine.get_price_db_connection')
    def test_returns_none_when_no_connection(self, mock_conn_fn):
        mock_conn_fn.return_value = None
        
        from modules.core.logger import AppLogger
        price, ts = get_latest_price_details(None, "SPY", "2026-02-23 23:59:59", AppLogger("test"))
        
        assert price is None
        assert ts is None


# ==========================================
# TEST: get_previous_session_stats
# ==========================================

class TestGetPreviousSessionStats:
    
    @patch('modules.analysis.impact_engine.get_price_db_connection')
    def test_returns_stats(self, mock_conn_fn):
        mock_conn = MagicMock()
        mock_conn_fn.return_value = mock_conn
        
        # First call: get previous date
        mock_rs_date = MagicMock()
        mock_rs_date.rows = [("2026-02-22",)]
        
        # Second call: get stats 
        mock_rs_stats = MagicMock()
        mock_rs_stats.rows = [(452.0, 448.0, 450.5)]
        
        mock_conn.execute.side_effect = [mock_rs_date, mock_rs_stats]
        
        from modules.core.logger import AppLogger
        stats = get_previous_session_stats(None, "SPY", "2026-02-23", AppLogger("test"))
        
        assert stats["yesterday_high"] == 452.0
        assert stats["yesterday_low"] == 448.0
        assert stats["yesterday_close"] == 450.5

    @patch('modules.analysis.impact_engine.get_price_db_connection')
    def test_returns_zeros_when_no_data(self, mock_conn_fn):
        mock_conn = MagicMock()
        mock_conn_fn.return_value = mock_conn
        mock_rs = MagicMock()
        mock_rs.rows = []
        mock_conn.execute.return_value = mock_rs
        
        from modules.core.logger import AppLogger
        stats = get_previous_session_stats(None, "SPY", "2026-02-23", AppLogger("test"))
        
        assert stats["yesterday_close"] == 0
        assert stats["yesterday_high"] == 0
        assert stats["yesterday_low"] == 0

    @patch('modules.analysis.impact_engine.get_price_db_connection')
    def test_returns_zeros_when_no_connection(self, mock_conn_fn):
        mock_conn_fn.return_value = None
        
        from modules.core.logger import AppLogger
        stats = get_previous_session_stats(None, "SPY", "2026-02-23", AppLogger("test"))
        
        assert stats["yesterday_close"] == 0
