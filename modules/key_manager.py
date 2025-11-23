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
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# --- TABLE 2: STATUS (Independent Buckets) ---
# Note: lifetime_count is removed as per your "No Combined Life" requirement.
CREATE_STATUS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS gemini_key_status (
    key_hash TEXT PRIMARY KEY NOT NULL,
    strikes INTEGER NOT NULL DEFAULT 0,
    release_time REAL NOT NULL DEFAULT 0,
    daily_count INTEGER NOT NULL DEFAULT 0,
    last_success_day TEXT NOT NULL DEFAULT '',
    
    -- Independent Usage Counters
    usage_pro INTEGER NOT NULL DEFAULT 0,
    usage_flash_2_5 INTEGER NOT NULL DEFAULT 0,
    usage_flash_2_0 INTEGER NOT NULL DEFAULT 0,
    
    last_used_ts REAL NOT NULL DEFAULT 0
);
"""

class KeyManager:
    # --- INDEPENDENT LIMITS (Per Key) ---
    LIMITS = {
        'gemini-2.5-pro': 50,
        'gemini-2.5-flash': 200,
        'gemini-2.0-flash': 200
    }

    DAILY_LIMIT = 10          
    MIN_INTERVAL_SEC = 30     
    
    COOLDOWN_PERIODS = {1: 60, 2: 600, 3: 3600, 4: 86400, 5: 259200}
    MAX_STRIKES = 5
    FATAL_STRIKE_COUNT = 999

    def __init__(self, db_url: str, auth_token: str):
        self.db_url = db_url
        self.auth_token = auth_token
        
        try:
            self.db_client = libsql_client.create_client_sync(url=db_url, auth_token=auth_token)
            self.db_client.execute(CREATE_KEYS_TABLE_SQL)
            self.db_client.execute(CREATE_STATUS_TABLE_SQL)
            
            # Ensure schema matches code expectation
            self._validate_schema_or_die()

        except Exception as e:
            log.critical(f"DB Connection failed: {e}")
            raise

        self.name_to_key = {}
        self.key_to_name = {}
        self.key_to_hash = {}
        self.available_keys = deque()
        self.cooldown_keys = {}
        self.key_failure_strikes = {}
        self.dead_keys = set()
        
        self._refresh_keys_from_db()

    def _validate_schema_or_die(self):
        """Ensures DB has the new usage columns."""
        try:
            rs = self.db_client.execute("SELECT * FROM gemini_key_status LIMIT 0")
            cols = list(rs.columns)
            required = ['usage_pro', 'usage_flash_2_5', 'usage_flash_2_0', 'daily_count']
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
    def add_key(self, name: str, value: str, display_order: int = 10):
        try:
            self.db_client.execute(
                "INSERT INTO gemini_api_keys (key_name, key_value, priority) VALUES (?, ?, ?)", 
                [name, value, display_order]
            )
            self._refresh_keys_from_db()
            return True, "Key added."
        except Exception as e: return False, str(e)

    def update_key(self, name: str, new_value: str):
        try:
            self.db_client.execute("UPDATE gemini_api_keys SET key_value = ?, added_at = CURRENT_TIMESTAMP WHERE key_name = ?", [new_value, name])
            self._refresh_keys_from_db()
            return True, "Updated."
        except Exception as e: return False, str(e)
        
    def update_key_order(self, updates: list[tuple]):
        try:
            for prio, name in updates:
                self.db_client.execute("UPDATE gemini_api_keys SET priority = ? WHERE key_name = ?", [prio, name])
            self._refresh_keys_from_db()
            return True, "Order updated."
        except Exception as e: return False, str(e)

    def delete_key(self, name: str):
        try:
            self.db_client.execute("DELETE FROM gemini_api_keys WHERE key_name = ?", [name])
            self._refresh_keys_from_db()
            return True, "Deleted."
        except Exception as e: return False, str(e)

    def get_all_managed_keys(self):
        rs = self.db_client.execute("SELECT key_name, key_value, priority, added_at FROM gemini_api_keys ORDER BY priority ASC, key_name ASC")
        if not rs.rows: return []
        return [self._row_to_dict(rs.columns, row) for row in rs.rows]

    def get_keys_for_account(self, account_prefix: str):
        all_keys = self.get_all_managed_keys()
        return [k for k in all_keys if account_prefix.lower() in k['key_name'].lower()]

    # --- CORE LOGIC ---

    def _refresh_keys_from_db(self):
        # 1. Load Keys
        keys_rs = self.db_client.execute("SELECT key_name, key_value FROM gemini_api_keys")
        self.name_to_key = {}
        if keys_rs.rows:
            for row in keys_rs.rows:
                d = self._row_to_dict(keys_rs.columns, row)
                self.name_to_key[d["key_name"]] = d["key_value"]

        self.key_to_name = {v: k for k, v in self.name_to_key.items()}
        all_real_keys = list(self.name_to_key.values())
        self.key_to_hash = {k: self._hash_key(k) for k in all_real_keys}

        # 2. Load Status
        status_rs = self.db_client.execute("SELECT * FROM gemini_key_status")
        db_state = {}
        if status_rs.rows:
            for row in status_rs.rows:
                row_dict = self._row_to_dict(status_rs.columns, row)
                db_state[row_dict["key_hash"]] = row_dict

        # 3. Reset pools
        self.available_keys = deque()
        self.cooldown_keys = {}
        self.key_failure_strikes = {}
        self.dead_keys = set()
        
        current_time = time.time()
        current_day = self._get_current_day()

        for key in all_real_keys:
            key_hash = self.key_to_hash[key]
            state = db_state.get(key_hash)
            
            if state:
                strikes = state.get("strikes", 0)
                release_time = state.get("release_time", 0)
                daily = state.get("daily_count", 0)
                last_day = state.get("last_success_day", "")
                
                if last_day != current_day: daily = 0
                
                # Global Blockers: Fatal Strikes or Daily Cap
                if strikes >= self.FATAL_STRIKE_COUNT:
                    self.key_failure_strikes[key] = strikes
                    self.dead_keys.add(key)
                    continue

                if daily >= self.DAILY_LIMIT:
                    self.cooldown_keys[key] = current_time + 86400 
                    self.key_failure_strikes[key] = strikes
                    continue

                self.key_failure_strikes[key] = strikes
                
                if current_time >= release_time:
                    self.available_keys.append(key)
                else:
                    self.cooldown_keys[key] = release_time
            else:
                self.available_keys.append(key)
                self.key_failure_strikes[key] = 0

        random.shuffle(self.available_keys)

    def get_key(self, target_model: str, exclude_name=None) -> tuple[str | None, str | None, float]:
        """
        Retrieves a valid API key.
        
        Returns:
            (key_name, key_value, wait_time)
        """
        self._reclaim_keys()
        current_time = time.time()
        valid_rotation = deque()
        
        while self.available_keys:
            # 1. Get Key Secret
            key_value = self.available_keys.popleft()
            # 2. Get Key Name (Critical for Observability)
            key_name = self.key_to_name.get(key_value, "Unknown")
            
            # 3. Exclude Check
            if exclude_name and key_name == exclude_name:
                valid_rotation.append(key_value) 
                continue

            # 4. RPM Check (DB Fail-Safe)
            key_hash = self.key_to_hash[key_value]
            try:
                # Ask the Brain: "When was this key last used?"
                rs = self.db_client.execute("SELECT last_used_ts FROM gemini_key_status WHERE key_hash = ?", [key_hash])
                last_used = rs.rows[0][0] if rs.rows else 0
            except Exception as e:
                # CRITICAL SAFETY NET: If DB fails, assume key is unsafe.
                log.error(f"KeyManager DB Read Failed for {key_name}: {e}")
                valid_rotation.append(key_value)
                self.available_keys.extend(valid_rotation) # Put everything back
                return None, None, 5.0 # Signal system failure/wait

            if (current_time - last_used) < self.MIN_INTERVAL_SEC:
                valid_rotation.append(key_value)
                continue
            
            # 5. INDEPENDENT BUCKET CHECK (Model Validity)
            limit = self.LIMITS.get(target_model, 50) 
            
            # Re-fetch state safely or use what we have (for buckets)
            # To be safe, we should fetch state, but for performance we can try-catch
            try:
                 rs_state = self.db_client.execute("SELECT * FROM gemini_key_status WHERE key_hash = ?", [key_hash])
                 state = self._row_to_dict(rs_state.columns, rs_state.rows[0]) if rs_state.rows else {}
            except: state = {}

            if target_model == 'gemini-2.5-pro':
                used = state.get("usage_pro", 0)
            elif target_model == 'gemini-2.5-flash':
                used = state.get("usage_flash_2_5", 0)
            elif target_model == 'gemini-2.0-flash':
                used = state.get("usage_flash_2_0", 0)
            else:
                used = 0 # Default safe
            
            if used >= limit:
                # Key is dead for THIS model. Skip.
                valid_rotation.append(key_value)
                continue

            # SUCCESS: Found valid key
            self.available_keys.extendleft(reversed(valid_rotation))
            return key_name, key_value, 0.0

        # FAILURE: No keys available
        self.available_keys.extend(valid_rotation)
        if not self.cooldown_keys: return None, None, 5.0
        return None, None, max(0, min(self.cooldown_keys.values()) - current_time)

    def get_specific_key(self, name: str):
        return self.name_to_key.get(name)

    def _reclaim_keys(self):
        current_time = time.time()
        released = [k for k, t in self.cooldown_keys.items() if current_time >= t]
        if not released: return
        random.shuffle(released)
        for key in released:
            del self.cooldown_keys[key]
            self.available_keys.append(key)

    def report_success(self, key: str, model_id: str = "default"):
        """Increments the SPECIFIC model counter."""
        key_hash = self.key_to_hash[key]
        try:
            rs = self.db_client.execute("SELECT * FROM gemini_key_status WHERE key_hash = ?", [key_hash])
            state = self._row_to_dict(rs.columns, rs.rows[0]) if rs.rows else {}
        except: state = {}
            
        new_d = state.get("daily_count", 0) + 1
        
        u_pro = state.get("usage_pro", 0)
        u_f25 = state.get("usage_flash_2_5", 0)
        u_f20 = state.get("usage_flash_2_0", 0)
        
        if model_id == 'gemini-2.5-pro': u_pro += 1
        elif model_id == 'gemini-2.5-flash': u_f25 += 1
        elif model_id == 'gemini-2.0-flash': u_f20 += 1
        
        self.key_failure_strikes[key] = 0
        self.available_keys.append(key)
        
        self._db_update_full(key_hash, 0, 0, new_d, self._get_current_day(), u_pro, u_f25, u_f20, time.time())

    def report_failure(self, key: str, is_server_error=False):
        if is_server_error:
            self.available_keys.append(key)
            return
        strikes = self.key_failure_strikes.get(key, 0) + 1
        self.key_failure_strikes[key] = strikes
        penalty = self.COOLDOWN_PERIODS.get(strikes, self.COOLDOWN_PERIODS[self.MAX_STRIKES])
        
        key_hash = self.key_to_hash[key]
        try:
            rs = self.db_client.execute("SELECT * FROM gemini_key_status WHERE key_hash = ?", [key_hash])
            state = self._row_to_dict(rs.columns, rs.rows[0]) if rs.rows else {}
        except: state = {}
        
        u_pro = state.get("usage_pro", 0)
        u_f25 = state.get("usage_flash_2_5", 0)
        u_f20 = state.get("usage_flash_2_0", 0)
        d = state.get("daily_count", 0)
            
        self.cooldown_keys[key] = time.time() + penalty
        self._db_update_full(key_hash, strikes, time.time() + penalty, d, self._get_current_day(), u_pro, u_f25, u_f20, time.time())

    def report_fatal_error(self, key: str, reason="FATAL"):
        strikes = self.FATAL_STRIKE_COUNT
        self.key_failure_strikes[key] = strikes
        self.dead_keys.add(key)
        if key in self.available_keys: self.available_keys = deque([k for k in self.available_keys if k != key])
        if key in self.cooldown_keys: del self.cooldown_keys[key]
        key_hash = self.key_to_hash[key]
        self.db_client.execute("UPDATE gemini_key_status SET strikes = ? WHERE key_hash = ?", [strikes, key_hash])

    def _db_update_full(self, hash, strikes, release, daily, day, u_pro, u_f25, u_f20, used_ts):
        sql = """
        INSERT INTO gemini_key_status (
            key_hash, strikes, release_time, daily_count, last_success_day, 
            usage_pro, usage_flash_2_5, usage_flash_2_0, last_used_ts
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(key_hash) DO UPDATE SET
        strikes=excluded.strikes, release_time=excluded.release_time, daily_count=excluded.daily_count,
        last_success_day=excluded.last_success_day, 
        usage_pro=excluded.usage_pro, usage_flash_2_5=excluded.usage_flash_2_5, usage_flash_2_0=excluded.usage_flash_2_0,
        last_used_ts=excluded.last_used_ts;
        """
        try: self.db_client.execute(sql, [hash, strikes, release, daily, day, u_pro, u_f25, u_f20, used_ts])
        except Exception as e: log.error(f"DB Update Error: {e}")