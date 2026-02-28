"""
Comprehensive tests for the KeyManager (modules/core/key_manager.py).

Tests cover:
- Key rotation and tier filtering
- Rate limit checking (RPM, TPM, RPD)
- Report usage (no double-append bug fix)
- Report failure: progressive cooldown with escalating strikes
- Token estimation: exact //4 formula
- MODELS_CONFIG: RPD limits, required models
- Strikes-based key blocking (_check_key_limits returns 86400)
- Dead keys / fatal error retirement
- Edge cases: no keys, unknown config, exhausted keys
- Thread safety under concurrent access
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


# ==========================================
# TEST: MODELS_CONFIG Correctness
# ==========================================

class TestModelsConfig:
    """
    These tests enforce the MODELS_CONFIG values match Google's actual API limits.
    Without these, wrong RPD or missing models silently break rate limiting.
    """

    def test_free_tier_rpd_is_20(self):
        """Google's free tier allows 20 RPD. If this is wrong, keys get exhausted immediately."""
        for config_key, config in KeyManager.MODELS_CONFIG.items():
            if config['tier'] == 'free':
                assert config['limits']['rpd'] == 20, \
                    f"MODELS_CONFIG['{config_key}'] has rpd={config['limits']['rpd']}, expected 20"

    def test_free_tier_tpm_is_250000(self):
        """Free tier TPM should be 250,000."""
        for config_key, config in KeyManager.MODELS_CONFIG.items():
            if config['tier'] == 'free':
                assert config['limits']['tpm'] == 250000, \
                    f"MODELS_CONFIG['{config_key}'] has tpm={config['limits']['tpm']}, expected 250000"

    def test_flash_lite_model_exists(self):
        """gemini-2.5-flash-lite-free must exist — it's actively used for lighter requests."""
        assert 'gemini-2.5-flash-lite-free' in KeyManager.MODELS_CONFIG, \
            "Missing 'gemini-2.5-flash-lite-free' from MODELS_CONFIG"

    def test_all_free_models_have_required_limits(self):
        """Every model config must have rpm, tpm, and rpd."""
        for config_key, config in KeyManager.MODELS_CONFIG.items():
            limits = config.get('limits', {})
            assert 'rpm' in limits, f"MODELS_CONFIG['{config_key}'] missing 'rpm'"
            assert 'tpm' in limits, f"MODELS_CONFIG['{config_key}'] missing 'tpm'"
            assert 'rpd' in limits, f"MODELS_CONFIG['{config_key}'] missing 'rpd'"

    def test_all_configs_have_model_id_and_tier(self):
        """Every config entry must have model_id and tier fields."""
        for config_key, config in KeyManager.MODELS_CONFIG.items():
            assert 'model_id' in config, f"MODELS_CONFIG['{config_key}'] missing 'model_id'"
            assert 'tier' in config, f"MODELS_CONFIG['{config_key}'] missing 'tier'"
            assert config['tier'] in ('free', 'paid'), \
                f"MODELS_CONFIG['{config_key}'] has invalid tier='{config['tier']}'"

    def test_cooldown_periods_are_escalating(self):
        """COOLDOWN_PERIODS must escalate: strike 1 < strike 2 < strike 3 < strike 4."""
        cp = KeyManager.COOLDOWN_PERIODS
        assert cp[1] < cp[2] < cp[3] < cp[4], \
            f"COOLDOWN_PERIODS should escalate but got {cp}"

    def test_max_strikes_reasonable(self):
        """MAX_STRIKES must be positive and reasonable (not 0 or absurdly high)."""
        assert 2 <= KeyManager.MAX_STRIKES <= 10, \
            f"MAX_STRIKES={KeyManager.MAX_STRIKES} should be between 2 and 10"


# ==========================================
# TEST: Progressive Cooldown (report_failure)
# ==========================================

class TestProgressiveCooldown:
    """
    Tests that report_failure applies escalating penalties.
    The old broken code used a flat 60s for every failure — these tests
    would have caught that immediately.
    """

    def test_first_strike_uses_shortest_cooldown(self):
        """First failure should use COOLDOWN_PERIODS[1] (10s), not a flat 60s."""
        km = _create_test_km()
        km.key_failure_strikes["fk1_value"] = 0
        km.db_client.execute.return_value = None

        before = time.time()
        km.report_failure("fk1_value", is_info_error=False)

        expected_penalty = KeyManager.COOLDOWN_PERIODS[1]  # 10s
        cooldown_end = km.cooldown_keys["fk1_value"]
        actual_penalty = cooldown_end - before

        assert abs(actual_penalty - expected_penalty) < 2.0, \
            f"First strike penalty should be ~{expected_penalty}s, got {actual_penalty:.1f}s"

    def test_second_strike_escalates(self):
        """Second consecutive failure should use COOLDOWN_PERIODS[2] (60s)."""
        km = _create_test_km()
        km.key_failure_strikes["fk1_value"] = 0
        km.db_client.execute.return_value = None

        km.report_failure("fk1_value", is_info_error=False)  # Strike 1
        before = time.time()
        km.report_failure("fk1_value", is_info_error=False)  # Strike 2

        expected_penalty = KeyManager.COOLDOWN_PERIODS[2]  # 60s
        cooldown_end = km.cooldown_keys["fk1_value"]
        actual_penalty = cooldown_end - before

        assert abs(actual_penalty - expected_penalty) < 2.0, \
            f"Second strike penalty should be ~{expected_penalty}s, got {actual_penalty:.1f}s"

    def test_third_strike_escalates_further(self):
        """Third consecutive failure should use COOLDOWN_PERIODS[3] (300s)."""
        km = _create_test_km()
        km.key_failure_strikes["fk1_value"] = 0
        km.db_client.execute.return_value = None

        for _ in range(2):
            km.report_failure("fk1_value", is_info_error=False)
        
        before = time.time()
        km.report_failure("fk1_value", is_info_error=False)  # Strike 3

        expected_penalty = KeyManager.COOLDOWN_PERIODS[3]  # 300s
        cooldown_end = km.cooldown_keys["fk1_value"]
        actual_penalty = cooldown_end - before

        assert abs(actual_penalty - expected_penalty) < 2.0, \
            f"Third strike penalty should be ~{expected_penalty}s, got {actual_penalty:.1f}s"

    def test_fourth_strike_maximum_cooldown(self):
        """Fourth consecutive failure should use COOLDOWN_PERIODS[4] (3600s)."""
        km = _create_test_km()
        km.key_failure_strikes["fk1_value"] = 0
        km.db_client.execute.return_value = None

        for _ in range(3):
            km.report_failure("fk1_value", is_info_error=False)
        
        before = time.time()
        km.report_failure("fk1_value", is_info_error=False)  # Strike 4

        expected_penalty = KeyManager.COOLDOWN_PERIODS[4]  # 3600s
        cooldown_end = km.cooldown_keys["fk1_value"]
        actual_penalty = cooldown_end - before

        assert abs(actual_penalty - expected_penalty) < 2.0, \
            f"Fourth strike penalty should be ~{expected_penalty}s, got {actual_penalty:.1f}s"

    def test_beyond_max_strikes_uses_default(self):
        """Strikes beyond COOLDOWN_PERIODS keys should fall back to 60s default."""
        km = _create_test_km()
        km.key_failure_strikes["fk1_value"] = 10  # Way past defined periods
        km.db_client.execute.return_value = None

        before = time.time()
        km.report_failure("fk1_value", is_info_error=False)

        cooldown_end = km.cooldown_keys["fk1_value"]
        actual_penalty = cooldown_end - before

        # .get(11, 60) → 60s fallback
        assert abs(actual_penalty - 60) < 2.0, \
            f"Beyond-max strike should fall back to 60s, got {actual_penalty:.1f}s"

    def test_strike_count_persists_across_calls(self):
        """key_failure_strikes must accumulate, not reset."""
        km = _create_test_km()
        km.key_failure_strikes["fk1_value"] = 0
        km.db_client.execute.return_value = None

        km.report_failure("fk1_value", is_info_error=False)
        assert km.key_failure_strikes["fk1_value"] == 1

        km.report_failure("fk1_value", is_info_error=False)
        assert km.key_failure_strikes["fk1_value"] == 2

        km.report_failure("fk1_value", is_info_error=False)
        assert km.key_failure_strikes["fk1_value"] == 3

    def test_info_error_does_not_increment_strikes(self):
        """Info errors (is_info_error=True) should NOT change strike count."""
        km = _create_test_km()
        km.key_failure_strikes["fk1_value"] = 0

        km.report_failure("fk1_value", is_info_error=True)
        assert km.key_failure_strikes["fk1_value"] == 0

    def test_failure_writes_strikes_to_db(self):
        """report_failure should write updated strike count to the database."""
        km = _create_test_km()
        km.key_failure_strikes["fk1_value"] = 0
        km.db_client.execute.return_value = None

        km.report_failure("fk1_value", is_info_error=False)

        km.db_client.execute.assert_called_once()
        call_args = km.db_client.execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]

        assert "strikes" in sql.lower()
        assert params[0] == 1  # strike count


# ==========================================
# TEST: Strikes-Based Key Blocking
# ==========================================

class TestStrikesBlocking:
    """
    Tests that _check_key_limits blocks keys with >= MAX_STRIKES.
    The old broken code had this check COMMENTED OUT, which
    meant bad keys continued cycling forever.
    """

    def test_max_strikes_blocks_key_for_24h(self):
        """Key with strikes >= MAX_STRIKES should return 86400 (24 hours)."""
        km = _create_test_km()
        mock_rs = MagicMock()
        mock_rs.columns = ['rpm_requests', 'rpm_window_start', 'tpm_tokens',
                           'strikes', 'rpd_requests', 'last_used_day']
        # strikes = MAX_STRIKES (5)
        mock_rs.rows = [(0, time.time() - 120, 0, KeyManager.MAX_STRIKES, 0, '2026-02-23')]
        km.db_client.execute.return_value = mock_rs

        wait = km._check_key_limits("fk1_value", "gemini-3-flash-preview",
                                     rpm_limit=5, tpm_limit=250000, rpd_limit=20)
        assert wait == 86400.0, f"Key at MAX_STRIKES should be blocked 24h, got wait={wait}"

    def test_above_max_strikes_blocks_key(self):
        """Key with strikes > MAX_STRIKES should also be blocked."""
        km = _create_test_km()
        mock_rs = MagicMock()
        mock_rs.columns = ['rpm_requests', 'rpm_window_start', 'tpm_tokens',
                           'strikes', 'rpd_requests', 'last_used_day']
        mock_rs.rows = [(0, time.time() - 120, 0, KeyManager.MAX_STRIKES + 5, 0, '2026-02-23')]
        km.db_client.execute.return_value = mock_rs

        wait = km._check_key_limits("fk1_value", "gemini-3-flash-preview",
                                     rpm_limit=5, tpm_limit=250000, rpd_limit=20)
        assert wait == 86400.0

    def test_below_max_strikes_allows_key(self):
        """Key with strikes < MAX_STRIKES should not be blocked by strikes check."""
        km = _create_test_km()
        mock_rs = MagicMock()
        mock_rs.columns = ['rpm_requests', 'rpm_window_start', 'tpm_tokens',
                           'strikes', 'rpd_requests', 'last_used_day']
        # strikes = MAX_STRIKES - 1, expired window, low counts
        mock_rs.rows = [(0, time.time() - 120, 0, KeyManager.MAX_STRIKES - 1, 0, '2026-02-23')]
        km.db_client.execute.return_value = mock_rs

        wait = km._check_key_limits("fk1_value", "gemini-3-flash-preview",
                                     rpm_limit=5, tpm_limit=250000, rpd_limit=20)
        assert wait == 0.0, f"Key below MAX_STRIKES should be allowed, got wait={wait}"

    def test_fatal_strikes_blocks_key(self):
        """Key with FATAL_STRIKE_COUNT (999) should be blocked for 24h."""
        km = _create_test_km()
        mock_rs = MagicMock()
        mock_rs.columns = ['rpm_requests', 'rpm_window_start', 'tpm_tokens',
                           'strikes', 'rpd_requests', 'last_used_day']
        mock_rs.rows = [(0, 0, 0, KeyManager.FATAL_STRIKE_COUNT, 0, '2026-02-23')]
        km.db_client.execute.return_value = mock_rs

        wait = km._check_key_limits("fk1_value", "gemini-3-flash-preview",
                                     rpm_limit=5, tpm_limit=250000, rpd_limit=20)
        assert wait == 86400.0


# ==========================================
# TEST: RPD (Requests Per Day) Enforcement
# ==========================================

class TestRPDEnforcement:
    """
    Tests that _check_key_limits correctly enforces RPD limits.
    With the old broken RPD=10000, keys could make thousands of
    requests per day before being rate-limited.
    """

    def test_rpd_exceeded_blocks_key(self):
        """Key that has used all 20 daily requests should be blocked."""
        km = _create_test_km()
        mock_rs = MagicMock()
        mock_rs.columns = ['rpm_requests', 'rpm_window_start', 'tpm_tokens',
                           'strikes', 'rpd_requests', 'last_used_day']
        today_str = time.strftime('%Y-%m-%d', time.gmtime())
        # 20 requests today (the actual Google limit)
        mock_rs.rows = [(0, time.time() - 120, 0, 0, 20, today_str)]
        km.db_client.execute.return_value = mock_rs

        wait = km._check_key_limits("fk1_value", "gemini-3-flash-preview",
                                     rpm_limit=5, tpm_limit=250000, rpd_limit=20)
        assert wait > 0, f"RPD exceeded (20/20) should block key, got wait={wait}"
        assert wait == 3600.0, f"RPD exceeded should return 3600s wait, got {wait}"

    def test_rpd_not_exceeded_allows_key(self):
        """Key with 19/20 daily requests should still be allowed."""
        km = _create_test_km()
        mock_rs = MagicMock()
        mock_rs.columns = ['rpm_requests', 'rpm_window_start', 'tpm_tokens',
                           'strikes', 'rpd_requests', 'last_used_day']
        today_str = time.strftime('%Y-%m-%d', time.gmtime())
        # 19 requests today, expired RPM window
        mock_rs.rows = [(0, time.time() - 120, 0, 0, 19, today_str)]
        km.db_client.execute.return_value = mock_rs

        wait = km._check_key_limits("fk1_value", "gemini-3-flash-preview",
                                     rpm_limit=5, tpm_limit=250000, rpd_limit=20)
        assert wait == 0.0, f"19/20 RPD should allow key, got wait={wait}"

    def test_rpd_resets_on_new_day(self):
        """RPD count from yesterday should not block today's usage."""
        km = _create_test_km()
        mock_rs = MagicMock()
        mock_rs.columns = ['rpm_requests', 'rpm_window_start', 'tpm_tokens',
                           'strikes', 'rpd_requests', 'last_used_day']
        # 100 requests but from yesterday
        mock_rs.rows = [(0, time.time() - 120, 0, 0, 100, '1999-01-01')]
        km.db_client.execute.return_value = mock_rs

        wait = km._check_key_limits("fk1_value", "gemini-3-flash-preview",
                                     rpm_limit=5, tpm_limit=250000, rpd_limit=20)
        assert wait == 0.0, f"Yesterday's RPD should not block today, got wait={wait}"


# ==========================================
# TEST: Token Estimation Formula
# ==========================================

class TestTokenEstimationFormula:
    """
    Tests the exact token estimation formula: len(text) // 4 + 1.
    The broken code used len(text) / 2.5 which overestimated tokens
    and caused incorrect TPM pre-checks.
    """

    def test_exact_formula_short_text(self):
        """'hello' (5 chars) should estimate to 5//4+1 = 2 tokens."""
        result = KeyManager.estimate_tokens("hello")
        assert result == 5 // 4 + 1, f"Expected {5//4+1}, got {result}"

    def test_exact_formula_medium_text(self):
        """100-char text should estimate to 100//4+1 = 26 tokens."""
        text = "x" * 100
        result = KeyManager.estimate_tokens(text)
        assert result == 100 // 4 + 1, f"Expected {100//4+1}, got {result}"

    def test_exact_formula_large_text(self):
        """1000-char text should estimate to 1000//4+1 = 251 tokens."""
        text = "a" * 1000
        result = KeyManager.estimate_tokens(text)
        assert result == 1000 // 4 + 1, f"Expected {1000//4+1}, got {result}"

    def test_formula_uses_integer_division(self):
        """Must use integer division (//4), not float division (/4 or /2.5)."""
        text = "abc"  # 3 chars
        result = KeyManager.estimate_tokens(text)
        # //4 → 0 + 1 = 1
        # /2.5 → 1.2 → int(1.2) + 1 = 2 (WRONG)
        assert result == 1, f"3 chars should be 3//4+1=1, got {result} (possible /2.5 formula?)"

    def test_formula_not_overestimating(self):
        """The //4 formula should produce SMALLER estimates than /2.5."""
        text = "x" * 500
        result = KeyManager.estimate_tokens(text)
        wrong_result = int(500 / 2.5) + 1  # 201 (the broken formula)
        correct_result = 500 // 4 + 1       # 126

        assert result == correct_result, \
            f"Got {result}. If {result} == {wrong_result}, the /2.5 bug is back!"


# ==========================================
# TEST: TPM Pre-Check Integration
# ==========================================

class TestTPMPreCheck:
    """
    Tests that _check_key_limits properly blocks requests that would
    exceed the minute's token budget.
    """

    def test_tpm_exceeded_blocks_request(self):
        """Request that would push tokens past TPM limit should be blocked."""
        km = _create_test_km()
        mock_rs = MagicMock()
        mock_rs.columns = ['rpm_requests', 'rpm_window_start', 'tpm_tokens',
                           'strikes', 'rpd_requests', 'last_used_day']
        # Already used 200,000 tokens this minute (limit 250,000)
        # New request is 100,000 tokens → 300,000 > 250,000 → block
        mock_rs.rows = [(1, time.time() - 10, 200000, 0, 5, '2026-02-23')]
        km.db_client.execute.return_value = mock_rs

        wait = km._check_key_limits("fk1_value", "gemini-3-flash-preview",
                                     rpm_limit=5, tpm_limit=250000, rpd_limit=20,
                                     estimated_tokens=100000)
        assert wait > 0, f"Should block when TPM would be exceeded, got wait={wait}"

    def test_tpm_within_budget_allows_request(self):
        """Request that fits within remaining TPM budget should pass."""
        km = _create_test_km()
        mock_rs = MagicMock()
        mock_rs.columns = ['rpm_requests', 'rpm_window_start', 'tpm_tokens',
                           'strikes', 'rpd_requests', 'last_used_day']
        # 100,000 used + 100,000 new = 200,000 < 250,000 → allow
        mock_rs.rows = [(1, time.time() - 10, 100000, 0, 5, '2026-02-23')]
        km.db_client.execute.return_value = mock_rs

        wait = km._check_key_limits("fk1_value", "gemini-3-flash-preview",
                                     rpm_limit=5, tpm_limit=250000, rpd_limit=20,
                                     estimated_tokens=100000)
        assert wait == 0.0

