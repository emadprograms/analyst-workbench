import time
from collections import deque
import logging
import random
import hashlib  # For hashing keys
import libsql_client  # For Turso

# Set up a basic logger for the manager
# This will be picked up by Streamlit's main logger
log = logging.getLogger(__name__)

# --- SQL to create the persistent table ---
# Using IF NOT EXISTS makes this command safe to run every time.
# It will only create the table on the very first run.
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS gemini_key_status (
    key_hash TEXT PRIMARY KEY NOT NULL,
    strikes INTEGER NOT NULL DEFAULT 0,
    release_time REAL NOT NULL DEFAULT 0
);
"""

class KeyManager:
    """
    Manages a pool of API keys with a progressive cooldown system,
    backed by a persistent Turso database.
    
    This (SYNC) version is designed to be initialized at the top level
    of a Streamlit app, before the main async event loop has started.
    """
    
    # Define the penalty for each consecutive failure (in seconds)
    COOLDOWN_PERIODS = {
        1: 60,           # Strike 1: 1 minute
        2: 600,          # Strike 2: 10 minutes
        3: 3600,         # Strike 3: 1 hour
        4: 86400,        # Strike 4: 24 hours (1 day)
        5: 259200        # Strike 5+: 3 days (as requested)
    }
    MAX_STRIKES = 5

    def __init__(self, api_keys: list[str], db_url: str, auth_token: str):
        """
        Initializes the KeyManager and connects to the Turso DB
        in a synchronous (blocking) manner.
        
        Args:
            api_keys: A list of API key strings.
            db_url: The Turso DB URL (e.g., "libsql://...").
            auth_token: The Turso auth token.
        """
        if not api_keys:
            log.critical("KeyManager initialized with no API keys.")
            raise ValueError("API keys list cannot be empty.")
            
        # --- THE FIX: Use create_client_sync ---
        # This creates a SYNCHRONOUS client that works during
        # Streamlit's script initialization (before the loop starts).
        try:
            self.db_client = libsql_client.create_client_sync(
                url=db_url,
                auth_token=auth_token
            )
            
            # This .execute() call is now a sync call.
            # It will create the table if it doesn't exist.
            self.db_client.execute(CREATE_TABLE_SQL)
            log.info("KeyManager connected to Turso DB (sync) and ensured table 'gemini_key_status' exists.")
        except Exception as e:
            log.critical(f"CRITICAL: KeyManager failed to connect to Turso DB: {e}")
            raise
        # --- END OF FIX ---
            
        # --- Hashing ---
        # We store hashes in the DB, not real keys.
        # self.key_to_hash: { "real_key_abc": "hash_123" }
        self.key_to_hash = {key: self._hash_key(key) for key in api_keys}
        # self.hash_to_key: { "hash_123": "real_key_abc" }
        self.hash_to_key = {h: k for k, h in self.key_to_hash.items()}
            
        # --- In-memory runtime state ---
        self.available_keys = deque()
        self.cooldown_keys = {} # { "real_key_abc": 1678886460 }
        self.key_failure_strikes = {} # { "real_key_abc": 2 }
        
        # --- Load persistent state from DB ---
        self._load_state_from_db(api_keys)
        
        log.info(f"KeyManager initialized. {len(self.available_keys)} keys available, {len(self.cooldown_keys)} keys on cooldown.")

    def _hash_key(self, key: str) -> str:
        """Hashes a key for safe storage in the DB."""
        return hashlib.sha256(key.encode('utf-8')).hexdigest()

    def _load_state_from_db(self, all_real_keys: list[str]):
        """
        Loads the persistent strike/cooldown state from Turso
        and populates the in-memory runtime pools.
        """
        try:
            # This .execute() is now sync
            rs = self.db_client.execute("SELECT key_hash, strikes, release_time FROM gemini_key_status")
            
            # 1. Create a dict of the persistent state { hash: (strikes, release_time) }
            db_state = {row["key_hash"]: (row["strikes"], row["release_time"]) for row in rs.rows}
            
            current_time = time.time()
            
            # 2. Iterate over all REAL keys and sort them into runtime pools
            for key in all_real_keys:
                key_hash = self.key_to_hash[key]
                state = db_state.get(key_hash)
                
                if state:
                    strikes, release_time = state
                    self.key_failure_strikes[key] = strikes
                    
                    if current_time >= release_time:
                        # Cooldown expired, add to available pool
                        self.available_keys.append(key)
                        # Reset strikes if it was on cooldown but is now free
                        if strikes > 0:
                            self.key_failure_strikes[key] = 0
                            self._db_update_key_status(key_hash, 0, 0)
                    else:
                        # Still on cooldown
                        self.cooldown_keys[key] = release_time
                else:
                    # Key is not in the DB (e.g., a new key)
                    # It's healthy and available by default.
                    self.available_keys.append(key)
                    self.key_failure_strikes[key] = 0
            
            # 3. Shuffle the initially available keys
            random.shuffle(self.available_keys)
            
        except Exception as e:
            log.error(f"Error loading state from DB: {e}. Starting with all keys available.")
            self.available_keys = deque(all_real_keys)
            self.cooldown_keys = {}
            self.key_failure_strikes = {}

    def _reclaim_keys(self):
        """
        (Internal) Checks the in-memory cooldown pool and moves any
        released keys back to the available pool.
        """
        current_time = time.time()
        released_keys = []
        
        for key, release_time in self.cooldown_keys.items():
            if current_time >= release_time:
                released_keys.append(key)
        
        if not released_keys:
            return

        # Shuffle reclaimed keys to prevent "reset" bug
        random.shuffle(released_keys)
                
        for key in released_keys:
            # Remove from cooldown pool
            del self.cooldown_keys[key]
            # Add to available pool
            self.available_keys.append(key)
            log.info(f"Key '...{key[-4:]}' reclaimed from cooldown.")
            
            # On successful reclaim, reset its strikes in the DB.
            self.key_failure_strikes[key] = 0
            key_hash = self.key_to_hash[key]
            self._db_update_key_status(key_hash, 0, 0)
            
    def get_key(self) -> tuple[str | None, float]:
        """
        Gets the next available API key from the in-memory pool.
        Returns (key, wait_time)
        """
        self._reclaim_keys()
        
        if not self.available_keys:
            if not self.cooldown_keys:
                log.error("No available keys and no keys in cooldown. Key list was likely empty.")
                return (None, 0.0)
            # Find the key that will be free the soonest
            next_release_time = min(self.cooldown_keys.values())
            wait_time = max(0, next_release_time - time.time())
            return (None, wait_time)
            
        key = self.available_keys.popleft()
        return (key, 0.0)

    def report_success(self, key: str):
        """
        Reports a successful API call.
        Resets the key's strike count in memory and in the DB.
        """
        self.key_failure_strikes[key] = 0
        self.available_keys.append(key)
        
        # --- Persist state to DB ---
        key_hash = self.key_to_hash[key]
        self._db_update_key_status(key_hash, 0, 0)
        
        log.debug(f"Key '...{key[-4:]}' reported success. Strikes reset in DB.")

    def report_failure(self, key: str):
        """
        Reports a failed API call.
        Increments strikes and saves the new cooldown to memory and DB.
        """
        strikes = self.key_failure_strikes.get(key, 0) + 1
        self.key_failure_strikes[key] = strikes
        
        cooldown_duration = self.COOLDOWN_PERIODS.get(
            strikes, 
            self.COOLDOWN_PERIODS[self.MAX_STRIKES]
        )
        
        release_time = time.time() + cooldown_duration
        
        # Update in-memory state
        self.cooldown_keys[key] = release_time
        
        # --- Persist state to DB ---
        key_hash = self.key_to_hash[key]
        self._db_update_key_status(key_hash, strikes, release_time)
        
        log.warning(f"Key '...{key[-4:]}' reported failure. Strike {strikes}. On cooldown for {cooldown_duration}s. State saved to DB.")

    def _db_update_key_status(self, key_hash: str, strikes: int, release_time: float):
        """(Internal) Writes the new state for a key to the Turso DB."""
        # This is an "UPSERT" command.
        # It tries to INSERT. If the key_hash already exists, it will UPDATE.
        sql = """
            INSERT INTO gemini_key_status (key_hash, strikes, release_time)
            VALUES (?, ?, ?)
            ON CONFLICT(key_hash) DO UPDATE SET
                strikes = excluded.strikes,
                release_time = excluded.release_time;
        """
        try:
            # This .execute() is now sync
            self.db_client.execute(sql, [key_hash, strikes, release_time])
        except Exception as e:
            log.error(f"CRITICAL: Failed to write key state to Turso DB! Hash: {key_hash}, Error: {e}")

    def get_status(self) -> dict:
        """
        Returns a dictionary of the current manager state for display.
        (Useful for debugging in Streamlit)
        """
        current_time = time.time()
        
        # Create a snapshot of the cooldown pool for display
        cooldown_status = {
            f"...{key[-4:]}": {
                "release_in_seconds": max(0, int(release_time - current_time)),
                "strikes": self.key_failure_strikes.get(key, 0)
            }
            for key, release_time in self.cooldown_keys.items()
        }
        
        return {
            "available_keys_count": len(self.available_keys),
            "cooldown_keys_count": len(self.cooldown_keys),
            "available_keys_display": [f"...{key[-4:]}" for key in self.available_keys],
            "cooldown_status": cooldown_status
        }