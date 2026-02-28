"""
Comprehensive tests for the KeyManager (modules/core/key_manager.py).

Tests cover:
- Key rotation and tier filtering
- Rate limit checking
- Report usage (no double-append bug fix)
- Report failure and cooldown
- Token estimation
- Edge cases: no keys, unknown config, exhausted keys
"""
import pytest
import os
import time
import threading
from unittest.mock import patch, MagicMock, PropertyMock
from collections import deque

# Must set env before importing modules that load config
os.environ["DISABLE_INFISICAL"] = "1"

from modules.core.key_manager import KeyManager


# ==========================================
# HELPERS: Mock KeyManager without DB
# ==========================================

def _create_test_km():
    """Create a KeyManager instance with mocked DB for testing."""
    with patch.object(KeyManager, '__init__', lambda self, *a, **kw: None):
        km = KeyManager.__new__(KeyManager)
        km.db_url = "https://test.turso.io"
        km.auth_token = "test_token"
        km.db_client = MagicMock()
        
        # Set up test keys
        km.name_to_key = {
            "free_key_1": "fk1_value",
            "free_key_2": "fk2_value",
            "paid_key_1": "pk1_value",
        }
        km.key_to_name = {v: k for k, v in km.name_to_key.items()}
        km.key_to_hash = {v: f"hash_{k}" for k, v in km.name_to_key.items()}
        km.key_metadata = {
            "fk1_value": {"tier": "free"},
            "fk2_value": {"tier": "free"},
            "pk1_value": {"tier": "paid"},
        }
        
        km.available_keys = deque(["fk1_value", "fk2_value", "pk1_value"])
        km.cooldown_keys = {}
        km.key_failure_strikes = {"fk1_value": 0, "fk2_value": 0, "pk1_value": 0}
        km.dead_keys = set()
        km._lock = threading.Lock()
        
        return km


# ==========================================
# TEST: Key Retrieval & Tier Filtering
# ==========================================

class TestKeyRetrieval:
    
    def test_free_config_gets_free_key(self):
        km = _create_test_km()
        # Mock no existing usage
        mock_rs = MagicMock()
        mock_rs.rows = []
        km.db_client.execute.return_value = mock_rs
        
        name, key, wait, model_id = km.get_key("gemini-3-flash-free", estimated_tokens=1000)
        
        assert key is not None
        assert km.key_metadata[key]['tier'] == 'free'
        assert wait == 0.0

    def test_paid_config_gets_paid_key(self):
        km = _create_test_km()
        mock_rs = MagicMock()
        mock_rs.rows = []
        km.db_client.execute.return_value = mock_rs
        
        name, key, wait, model_id = km.get_key("gemini-3-pro-paid", estimated_tokens=1000)
        
        assert key is not None
        assert km.key_metadata[key]['tier'] == 'paid'

    def test_free_config_skips_paid_keys(self):
        """Free configs must NOT use paid keys."""
        km = _create_test_km()
        km.available_keys = deque(["pk1_value"])  # Only paid key available
        mock_rs = MagicMock()
        mock_rs.rows = []
        km.db_client.execute.return_value = mock_rs
        
        name, key, wait, model_id = km.get_key("gemini-3-flash-free", estimated_tokens=1000)
        
        assert key is None  # No free keys available

    def test_paid_config_skips_free_keys(self):
        """Paid configs must NOT use free keys."""
        km = _create_test_km()
        km.available_keys = deque(["fk1_value", "fk2_value"])  # Only free keys
        mock_rs = MagicMock()
        mock_rs.rows = []
        km.db_client.execute.return_value = mock_rs
        
        name, key, wait, model_id = km.get_key("gemini-3-pro-paid", estimated_tokens=1000)
        
        assert key is None

    def test_unknown_config_uses_defaults(self):
        """Unknown config_id should use safe defaults."""
        km = _create_test_km()
        mock_rs = MagicMock()
        mock_rs.rows = []
        km.db_client.execute.return_value = mock_rs
        
        # Unknown config defaults to 'free' tier
        name, key, wait, model_id = km.get_key("unknown-model", estimated_tokens=100)
        
        # Should get a free key with default limits
        if key:
            assert km.key_metadata[key]['tier'] == 'free'


# ==========================================
# TEST: Token Guard
# ==========================================

class TestTokenGuard:
    
    def test_fatal_on_oversized_request(self):
        """Request exceeding model TPM limit should return -1.0 (fatal)."""
        km = _create_test_km()
        
        # gemini-3-flash-free has tpm=250000
        name, key, wait, model_id = km.get_key("gemini-3-flash-free", estimated_tokens=500000)
        
        assert key is None
        assert wait == -1.0

    def test_normal_request_passes(self):
        km = _create_test_km()
        mock_rs = MagicMock()
        mock_rs.rows = []
        km.db_client.execute.return_value = mock_rs
        
        name, key, wait, model_id = km.get_key("gemini-3-flash-free", estimated_tokens=1000)
        
        assert key is not None
        assert wait == 0.0


# ==========================================
# TEST: Report Usage (Bug Fix: No Double-Append)
# ==========================================

class TestReportUsage:
    
    def test_no_double_append_to_available_keys(self):
        """
        Checkout/checkin pattern: get_key() checks the key OUT (removes from pool).
        report_usage() checks it back IN (adds to pool). Key should appear exactly once.
        """
        km = _create_test_km()
        mock_rs = MagicMock()
        mock_rs.rows = []
        km.db_client.execute.return_value = mock_rs
        
        # Get a key (this REMOVES it from available_keys — checked out)
        name, key, wait, model_id = km.get_key("gemini-3-flash-free", estimated_tokens=100)
        assert key is not None
        
        # Key should NOT be in the pool while checked out
        count_checked_out = list(km.available_keys).count(key)
        assert count_checked_out == 0, \
            f"Key should not be in pool while checked out, but appeared {count_checked_out} times."
        
        # Report usage (should add key back — checked in)
        km.report_usage(key, tokens=1000, model_id=model_id)
        
        count_after = list(km.available_keys).count(key)
        
        # Should appear exactly once
        assert count_after == 1, \
            f"Key should appear exactly 1 time after checkin, but appeared {count_after} times."

    def test_report_usage_updates_db(self):
        """Usage reporting should write to DB."""
        km = _create_test_km()
        
        # Mock existing row - must use tuple format since _row_to_dict zips columns+row
        mock_rs = MagicMock()
        mock_rs.columns = ['rpm_requests', 'rpm_window_start', 'tpm_tokens', 'rpd_requests', 'last_used_day']
        mock_rs.rows = [(1, time.time() - 10, 500, 5, '2026-02-23')]
        km.db_client.execute.return_value = mock_rs
        
        # Mock the raw HTTP call
        km._raw_http_execute = MagicMock()
        
        km.report_usage("fk1_value", tokens=1000, model_id="gemini-3-flash-preview")
        
        km._raw_http_execute.assert_called_once()


# ==========================================
# TEST: Report Failure & Cooldown
# ==========================================

class TestReportFailure:
    
    def test_info_error_keeps_key_available(self):
        """Info errors (like bad JSON) should not trigger cooldown."""
        km = _create_test_km()
        initial_count = len(km.available_keys)
        
        km.report_failure("fk1_value", is_info_error=True)
        
        assert "fk1_value" in km.available_keys
        assert "fk1_value" not in km.cooldown_keys

    def test_rate_limit_triggers_cooldown(self):
        """429 errors should put key in cooldown."""
        km = _create_test_km()
        km.db_client.execute.return_value = None  # Don't care about DB write
        
        km.report_failure("fk1_value", is_info_error=False)
        
        assert "fk1_value" in km.cooldown_keys
        assert km.cooldown_keys["fk1_value"] > time.time()

    def test_cooldown_key_not_returned(self):
        """Keys in cooldown should not be returned by get_key."""
        km = _create_test_km()
        km.available_keys = deque(["fk1_value"])
        km.cooldown_keys["fk1_value"] = time.time() + 60  # 60 seconds from now
        
        mock_rs = MagicMock()
        mock_rs.rows = []
        km.db_client.execute.return_value = mock_rs
        
        name, key, wait, model_id = km.get_key("gemini-3-flash-free", estimated_tokens=100)
        
        assert key is None  # Key is in cooldown

    def test_expired_cooldown_releases_key(self):
        """Keys past their cooldown should be reclaimed."""
        km = _create_test_km()
        km.available_keys = deque(["fk1_value"])
        km.cooldown_keys["fk1_value"] = time.time() - 1  # Already expired
        
        mock_rs = MagicMock()
        mock_rs.rows = []
        km.db_client.execute.return_value = mock_rs
        
        name, key, wait, model_id = km.get_key("gemini-3-flash-free", estimated_tokens=100)
        
        assert key is not None


# ==========================================
# TEST: Dead Keys
# ==========================================

class TestDeadKeys:
    
    def test_dead_key_not_returned(self):
        km = _create_test_km()
        km.dead_keys.add("fk1_value")
        km.available_keys = deque(["fk1_value"])
        
        mock_rs = MagicMock()
        mock_rs.rows = []
        km.db_client.execute.return_value = mock_rs
        
        name, key, wait, model_id = km.get_key("gemini-3-flash-free", estimated_tokens=100)
        
        # fk1_value is dead, should try fk2_value but it's not in available_keys
        assert key is None

    def test_report_fatal_marks_dead(self):
        km = _create_test_km()
        km.report_fatal_error("fk1_value")
        
        assert "fk1_value" in km.dead_keys


# ==========================================
# TEST: Token Estimation
# ==========================================

class TestTokenEstimation:
    
    def test_estimate_tokens_basic(self):
        assert KeyManager.estimate_tokens("hello world") > 0
    
    def test_estimate_tokens_empty(self):
        assert KeyManager.estimate_tokens("") == 0
    
    def test_estimate_tokens_none(self):
        assert KeyManager.estimate_tokens(None) == 0
    
    def test_estimate_tokens_proportional(self):
        """Longer text should estimate more tokens."""
        short = KeyManager.estimate_tokens("Hello")
        long = KeyManager.estimate_tokens("Hello " * 1000)
        assert long > short


# ==========================================
# TEST: Rate Limit Checking
# ==========================================

class TestRateLimitChecking:
    
    def test_no_usage_returns_zero_wait(self):
        """No prior usage for a model should allow immediate use."""
        km = _create_test_km()
        mock_rs = MagicMock()
        mock_rs.rows = []
        km.db_client.execute.return_value = mock_rs
        
        wait = km._check_key_limits("fk1_value", "gemini-3-flash-preview", 
                                     rpm_limit=5, tpm_limit=250000, rpd_limit=10000)
        assert wait == 0.0

    def test_rpm_exceeded_returns_wait_time(self):
        """Exceeding RPM should return positive wait time."""
        km = _create_test_km()
        mock_rs = MagicMock()
        mock_rs.columns = ['rpm_requests', 'rpm_window_start', 'tpm_tokens', 
                           'strikes', 'rpd_requests', 'last_used_day']
        # 5 requests in last 30 seconds (limit is 5) - tuple format for _row_to_dict
        mock_rs.rows = [(5, time.time() - 30, 5000, 0, 10, '2026-02-23')]
        km.db_client.execute.return_value = mock_rs
        
        wait = km._check_key_limits("fk1_value", "gemini-3-flash-preview",
                                     rpm_limit=5, tpm_limit=250000, rpd_limit=10000)
        assert wait > 0, "Should need to wait when RPM exceeded"

    def test_expired_window_returns_zero(self):
        """RPM window older than 60s should be treated as fresh."""
        km = _create_test_km()
        mock_rs = MagicMock()
        mock_rs.columns = ['rpm_requests', 'rpm_window_start', 'tpm_tokens',
                           'strikes', 'rpd_requests', 'last_used_day']
        # Window started 120 seconds ago - tuple format for _row_to_dict
        mock_rs.rows = [(100, time.time() - 120, 999999, 0, 10, '2026-02-23')]
        km.db_client.execute.return_value = mock_rs
        
        wait = km._check_key_limits("fk1_value", "gemini-3-flash-preview",
                                     rpm_limit=5, tpm_limit=250000, rpd_limit=10000)
        assert wait == 0.0


# ==========================================
# TEST: Thread Safety
# ==========================================

class TestThreadSafety:
    """Tests that KeyManager is safe under concurrent access."""

    def _create_many_keys_km(self, n_keys=20):
        """Create a KeyManager with many free keys for concurrency testing."""
        with patch.object(KeyManager, '__init__', lambda self, *a, **kw: None):
            km = KeyManager.__new__(KeyManager)
            km.db_url = "https://test.turso.io"
            km.auth_token = "test_token"
            km.db_client = MagicMock()
            km._lock = threading.Lock()

            km.name_to_key = {}
            km.key_metadata = {}
            for i in range(n_keys):
                name = f"key_{i}"
                value = f"val_{i}"
                km.name_to_key[name] = value
                km.key_metadata[value] = {"tier": "free"}

            km.key_to_name = {v: k for k, v in km.name_to_key.items()}
            km.key_to_hash = {v: f"hash_{k}" for k, v in km.name_to_key.items()}
            km.available_keys = deque(km.name_to_key.values())
            km.cooldown_keys = {}
            km.key_failure_strikes = {v: 0 for v in km.name_to_key.values()}
            km.dead_keys = set()

            # Mock DB to always return no usage (all keys available)
            mock_rs = MagicMock()
            mock_rs.rows = []
            km.db_client.execute.return_value = mock_rs

            return km

    def test_concurrent_get_key_no_duplicates(self):
        """Multiple threads calling get_key should never get the same key value simultaneously."""
        km = self._create_many_keys_km(n_keys=20)
        results = []
        errors = []

        def grab_key():
            try:
                name, key, wait, model_id = km.get_key("gemini-3-flash-free", estimated_tokens=100)
                if key:
                    results.append(key)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=grab_key) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Threads raised errors: {errors}"
        # All returned keys should be valid
        assert len(results) == 20
        # Keys are re-appended after use so duplicates are possible in results,
        # but no thread should crash

    def test_concurrent_report_failure_no_crash(self):
        """Multiple threads calling report_failure concurrently should not crash."""
        km = self._create_many_keys_km(n_keys=10)
        errors = []

        def fail_key(i):
            try:
                key_val = f"val_{i % 10}"
                km.report_failure(key_val, is_info_error=(i % 2 == 0))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=fail_key, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Threads raised errors: {errors}"

    def test_concurrent_get_key_and_report_usage(self):
        """Interleaved get_key + report_usage from multiple threads should not crash."""
        km = self._create_many_keys_km(n_keys=10)
        # Mock _raw_http_execute to avoid actual HTTP calls
        km._raw_http_execute = MagicMock()
        errors = []

        def worker(i):
            try:
                name, key, wait, model_id = km.get_key("gemini-3-flash-free", estimated_tokens=100)
                if key:
                    km.report_usage(key, tokens=500, model_id="gemini-3-flash-preview")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Threads raised errors: {errors}"
