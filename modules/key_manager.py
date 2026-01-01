from __future__ import annotations
import time
from collections import deque
import logging
import random
import hashlib
import libsql_client

log = logging.getLogger(__name__)

# --- TABLE 1: KEYS ---
CREATE_KEYS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS gemini_api_keys (
    key_name TEXT PRIMARY KEY NOT NULL,
    key_value TEXT NOT NULL,
    priority INTEGER DEFAULT 10,
    tier TEXT DEFAULT 'free', 
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# --- TABLE 2: STATUS (V5 - Gemma) ---
CREATE_STATUS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS gemini_key_status (
    key_hash TEXT PRIMARY KEY NOT NULL,
    strikes INTEGER NOT NULL DEFAULT 0,
    release_time REAL NOT NULL DEFAULT 0,
    last_success_day TEXT NOT NULL DEFAULT '',
    last_used_ts REAL NOT NULL DEFAULT 0,
    
    daily_free_lite INTEGER NOT NULL DEFAULT 0,
    daily_free_flash INTEGER NOT NULL DEFAULT 0,
    daily_free_gemma_27b INTEGER NOT NULL DEFAULT 0,
    daily_free_gemma_12b INTEGER NOT NULL DEFAULT 0,

    daily_3_pro INTEGER NOT NULL DEFAULT 0,
    daily_2_5_pro INTEGER NOT NULL DEFAULT 0,
    daily_2_0_flash INTEGER NOT NULL DEFAULT 0,

    ts_3_pro REAL NOT NULL DEFAULT 0,
    ts_2_5_pro REAL NOT NULL DEFAULT 0,
    ts_2_0_flash REAL NOT NULL DEFAULT 0
);
"""

class KeyManager:
    # --- CONFIGURATION ---
    TIER_FREE = 'free'
    TIER_PAID = 'paid'
    
    MIN_INTERVAL_SEC = 60 

    # MAPPINGS: Model -> (Count Column, Timestamp Column)
    MODELS_PAID = { 
        'gemini-3-pro-preview': ('daily_3_pro', 'ts_3_pro'), # Corrected ID
        'gemini-2.5-pro': ('daily_2_5_pro', 'ts_2_5_pro'),
        'gemini-2.0-flash': ('daily_2_0_flash', 'ts_2_0_flash')
    }
    
    # Free Tier Mapping: Model -> Count Column
    MODELS_FREE = {
        'gemini-2.5-flash-lite': 'daily_free_lite',
        'gemini-2.5-flash': 'daily_free_flash',
        'gemma-3-27b-it': 'daily_free_gemma_27b',
        'gemma-3-12b-it': 'daily_free_gemma_12b'
    }

    # Limits (Dynamic)
    LIMITS_FREE = {
        'gemini-2.5-flash-lite': 18,
        'gemini-2.5-flash': 18,
        'gemma-3-27b-it': 28,
        'gemma-3-12b-it': 28
    }
    
    LIMITS_PAID = {
        'gemini-3-pro-preview': 250, # Corrected ID
        'gemini-2.5-pro': 2000, # Increased from 100 per user request
        'gemini-2.0-flash': 1000
    }

    COOLDOWN_PERIODS = {1: 60, 2: 600, 3: 3600, 4: 86400} 
    MAX_STRIKES = 5
    FATAL_STRIKE_COUNT = 999

    def __init__(self, db_url: str, auth_token: str):
        self.db_url = db_url.replace("libsql://", "https://") # Force HTTPS
        self.auth_token = auth_token
        
        try:
            self.db_client = libsql_client.create_client_sync(url=self.db_url, auth_token=auth_token)
            self.db_client.execute(CREATE_KEYS_TABLE_SQL)
            self.db_client.execute(CREATE_STATUS_TABLE_SQL)
            self._validate_schema_or_die()
        except Exception as e:
            log.critical(f"DB Connection failed: {e}")
            raise

        self.name_to_key = {}
        self.key_to_name = {}
        self.key_to_hash = {}
        self.key_metadata = {} 
        
        self.available_keys = deque()
        self.cooldown_keys = {}
        self.key_failure_strikes = {}
        self.dead_keys = set()
        
        self._refresh_keys_from_db()

    def _validate_schema_or_die(self):
        try:
            rs = self.db_client.execute("SELECT * FROM gemini_key_status LIMIT 0")
            cols = list(rs.columns)
            required = ['daily_free_gemma_27b', 'ts_3_pro']
            missing = [c for c in required if c not in cols]
            if missing:
                raise RuntimeError(f"CRITICAL: DB missing columns {missing}. Run reset_db.py.")
        except Exception as e:
            if "CRITICAL" in str(e): raise e
            pass

    def _hash_key(self, key: str) -> str:
        return hashlib.sha256(key.encode('utf-8')).hexdigest()
    
    def _get_current_day(self):
        return time.strftime('%Y-%m-%d', time.gmtime())
    
    def _row_to_dict(self, columns, row):
        return dict(zip(columns, row))

    # --- CRUD ---
    def add_key(self, name: str, value: str, tier: str = 'free', display_order: int = 10):
        try:
            self.db_client.execute(
                "INSERT INTO gemini_api_keys (key_name, key_value, priority, tier) VALUES (?, ?, ?, ?)", 
                [name, value, display_order, tier]
            )
            self._refresh_keys_from_db()
            return True, "Key added."
        except Exception as e: return False, str(e)

    def update_key_tier(self, name: str, new_tier: str):
        try:
            self.db_client.execute("UPDATE gemini_api_keys SET tier = ? WHERE key_name = ?", [new_tier, name])
            self._refresh_keys_from_db()
            return True, "Updated Tier."
        except Exception as e: return False, str(e)

    def delete_key(self, name: str):
        try:
            self.db_client.execute("DELETE FROM gemini_api_keys WHERE key_name = ?", [name])
            self._refresh_keys_from_db()
            return True, "Deleted."
        except Exception as e: return False, str(e)

    def get_all_managed_keys(self):
        rs = self.db_client.execute("SELECT key_name, key_value, priority, tier, added_at FROM gemini_api_keys ORDER BY priority ASC, key_name ASC")
        if not rs.rows: return []
        return [self._row_to_dict(rs.columns, row) for row in rs.rows]

    # --- SIMPLE MODE (User Request) ---
    def get_simple_paid_key(self):
        """
        Directly fetches the first available PAID key. 
        No checks, no rotation, no logic.
        """
        try:
            rs = self.db_client.execute("SELECT key_value FROM gemini_api_keys WHERE tier='paid' ORDER BY priority ASC LIMIT 1")
            if rs.rows:
                return rs.rows[0][0]
            # Fallback to ANY key if no paid key found
            rs_any = self.db_client.execute("SELECT key_value FROM gemini_api_keys ORDER BY priority ASC LIMIT 1")
            return rs_any.rows[0][0] if rs_any.rows else None
        except Exception:
            return None

    # --- CORE LOGIC ---

    def _refresh_keys_from_db(self):
        keys_rs = self.db_client.execute("SELECT key_name, key_value, tier FROM gemini_api_keys")
        self.name_to_key = {}
        self.key_metadata = {}
        
        if keys_rs.rows:
            for row in keys_rs.rows:
                d = self._row_to_dict(keys_rs.columns, row)
                self.name_to_key[d["key_name"]] = d["key_value"]
                self.key_metadata[d["key_value"]] = {'tier': d.get('tier', 'free')}

        self.key_to_name = {v: k for k, v in self.name_to_key.items()}
        all_real_keys = list(self.name_to_key.values())
        self.key_to_hash = {k: self._hash_key(k) for k in all_real_keys}

        self.available_keys = deque()
        self.cooldown_keys = {}
        self.key_failure_strikes = {}
        self.dead_keys = set()
        
        for key in all_real_keys:
            self.available_keys.append(key)
            self.key_failure_strikes[key] = 0

        random.shuffle(self.available_keys)

    def get_key(self, target_model: str, exclude_name=None) -> tuple[str | None, str | None, float]:
        self._reclaim_keys()
        current_time = time.time()
        
        required_tier = self.TIER_PAID if target_model in self.MODELS_PAID else self.TIER_FREE
        valid_rotation = deque()
        min_cutoff_wait = float('inf') # Track minimum wait time needed
        
        # log.info(f"Checking keys for {target_model} (Required Tier: {required_tier})")
        
        keys_checked = 0
        while self.available_keys:
            key_value = self.available_keys.popleft()
            keys_checked += 1
            key_name = self.key_to_name.get(key_value, "Unknown")
            key_meta = self.key_metadata.get(key_value, {})
            key_tier = key_meta.get('tier', 'free')

            if key_tier != required_tier:
                # log.debug(f"Skipping {key_name}: Key Tier '{key_tier}' != Req '{required_tier}'")
                valid_rotation.append(key_value)
                continue 

            if exclude_name and key_name == exclude_name:
                valid_rotation.append(key_value) 
                continue

            # DB State Check
            key_hash = self.key_to_hash[key_value]
            try:
                rs = self.db_client.execute("SELECT * FROM gemini_key_status WHERE key_hash = ?", [key_hash])
                state = self._row_to_dict(rs.columns, rs.rows[0]) if rs.rows else {}
            except Exception as e:
                log.error(f"DB Error: {e}"); state = {}

            # --- RATE LIMIT CHECK (V4) ---
            rate_limited = False
            wait_for_this_key = 0.0
            
            if required_tier == self.TIER_FREE:
                # FREE: STRICT GLOBAL LOCK
                last_used = state.get('last_used_ts', 0)
                diff = current_time - last_used
                if diff < self.MIN_INTERVAL_SEC:
                    rate_limited = True
                    wait_for_this_key = self.MIN_INTERVAL_SEC - diff
            
            elif required_tier == self.TIER_PAID:
                # PAID: PER-MODEL LOCK
                config = self.MODELS_PAID.get(target_model) 
                if config:
                    ts_col = config[1]
                    last_model_ts = state.get(ts_col, 0)
                    diff = current_time - last_model_ts
                    
                    # --- HOTFIX: BYPASS LIMIT FOR 2.5 PRO and 3.0 PRO (User Request) ---
                    limit_to_use = 0 if target_model in ['gemini-2.5-pro', 'gemini-3-pro-preview'] else self.MIN_INTERVAL_SEC

                    if diff < limit_to_use:
                        rate_limited = True
                        wait_for_this_key = limit_to_use - diff
            
            if rate_limited:
                # log.info(f"Skipping {key_name}: Rate Limited. Wait {wait_for_this_key:.1f}s")
                if wait_for_this_key > 0:
                    min_cutoff_wait = min(min_cutoff_wait, wait_for_this_key)
                valid_rotation.append(key_value)
                continue

            # --- DAILY LIMIT CHECK ---
            current_day = self._get_current_day()
            db_day = state.get('last_success_day', '')
            is_new_day = db_day != current_day

            strikes = state.get('strikes', 0)
            if strikes >= self.FATAL_STRIKE_COUNT:
                self.dead_keys.add(key_value); continue

            usage_ok = True
            
            if required_tier == self.TIER_FREE:
                col_name = self.MODELS_FREE.get(target_model)
                if col_name:
                    count = 0 if is_new_day else state.get(col_name, 0)
                    # DYNAMIC LIMIT LOOKUP
                    limit = self.LIMITS_FREE.get(target_model, 18) 
                    if count >= limit: usage_ok = False
            
            elif required_tier == self.TIER_PAID:
                config = self.MODELS_PAID.get(target_model)
                if config:
                    count_col = config[0]
                    count = 0 if is_new_day else state.get(count_col, 0)
                    limit = self.LIMITS_PAID.get(target_model, 100)
                    if count >= limit: usage_ok = False
            
            if not usage_ok:
                valid_rotation.append(key_value); continue

            # Found good key
            self.available_keys.extendleft(reversed(valid_rotation))
            return key_name, key_value, 0.0

        self.available_keys.extend(valid_rotation)
        
        # Calculate dynamic wait
        final_wait = 5.0
        if min_cutoff_wait != float('inf'):
            final_wait = max(5.0, min_cutoff_wait + 1.0) # Add 1s buffer for safety
            
        return None, None, final_wait 

    def _reclaim_keys(self):
        current_time = time.time()
        released = [k for k, t in self.cooldown_keys.items() if current_time >= t]
        if not released: return
        for key in released:
            del self.cooldown_keys[key]
            self.available_keys.append(key)

    def report_success(self, key: str, model_id: str):
        key_hash = self.key_to_hash[key]
        current_day = self._get_current_day()
        current_ts = time.time()
        
        key_meta = self.key_metadata.get(key, {})
        key_tier = key_meta.get('tier', 'free')

        col_to_inc = 'daily_free_lite' # Fallback
        ts_col_update = "" 

        if key_tier == self.TIER_PAID:
            config = self.MODELS_PAID.get(model_id)
            if config:
                col_to_inc = config[0]
                ts_col_update = f", {config[1]} = {current_ts}" 
            else:
                col_to_inc = 'daily_3_pro' 
        else:
            col_to_inc = self.MODELS_FREE.get(model_id, 'daily_free_lite')

        try:
            rs = self.db_client.execute("SELECT last_success_day FROM gemini_key_status WHERE key_hash = ?", [key_hash])
            last_day = rs.rows[0][0] if rs.rows else ""
            
            if last_day != current_day:
                # Reset ALL counters (Including Gemma)
                cols = [
                    'daily_free_lite', 'daily_free_flash', 'daily_free_gemma_27b', 'daily_free_gemma_12b',
                    'daily_3_pro', 'daily_2_5_pro', 'daily_2_0_flash'
                ]
                set_clause = ", ".join([f"{c} = 0" for c in cols])
                
                sql = f"""
                INSERT INTO gemini_key_status (key_hash, last_success_day, last_used_ts, strikes, {col_to_inc})
                VALUES (?, ?, ?, 0, 1)
                ON CONFLICT(key_hash) DO UPDATE SET
                last_success_day = excluded.last_success_day,
                last_used_ts = excluded.last_used_ts,
                strikes = 0,
                {set_clause},
                {col_to_inc} = 1
                {ts_col_update};
                """
                self.db_client.execute(sql, [key_hash, current_day, current_ts])
            else:
                sql = f"""
                INSERT INTO gemini_key_status (key_hash, last_success_day, last_used_ts, {col_to_inc})
                VALUES (?, ?, ?, 1)
                ON CONFLICT(key_hash) DO UPDATE SET
                {col_to_inc} = gemini_key_status.{col_to_inc} + 1,
                last_used_ts = excluded.last_used_ts,
                last_success_day = excluded.last_success_day,
                strikes = 0
                {ts_col_update};
                """
                self.db_client.execute(sql, [key_hash, current_day, current_ts])
                
        except Exception as e:
            log.error(f"Report Success Failed: {e}")
            
        self.available_keys.append(key)

    def report_failure(self, key: str, is_server_error=False):
        if is_server_error:
            self.available_keys.append(key)
            return
            
        strikes = self.key_failure_strikes.get(key, 0) + 1
        self.key_failure_strikes[key] = strikes
        penalty = self.COOLDOWN_PERIODS.get(strikes, 86400)
        
        self.cooldown_keys[key] = time.time() + penalty
        
        try:
            key_hash = self.key_to_hash[key]
            self.db_client.execute(
                "UPDATE gemini_key_status SET strikes = ?, release_time = ? WHERE key_hash = ?", 
                [strikes, time.time() + penalty, key_hash]
            )
        except: pass

    def report_fatal_error(self, key: str):
        self.dead_keys.add(key)
        try:
            self.db_client.execute("UPDATE gemini_key_status SET strikes = 999 WHERE key_hash = ?", [self.key_to_hash[key]])
        except: pass