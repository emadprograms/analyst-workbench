import time
from collections import deque
import logging
import random  # <-- ADDED

# Set up a basic logger for the manager
log = logging.getLogger(__name__)
# To see the manager's logs, configure logging in your main app
# e.g., logging.basicConfig(level=logging.INFO)

class KeyManager:
    """
    Manages a pool of API keys with a progressive cooldown system.
    This class is intended to be used as a singleton (one instance).
    """
    
    # Define the penalty for each consecutive failure (in seconds)
    COOLDOWN_PERIODS = {
        1: 60,           # Strike 1: 1 minute
        2: 600,          # Strike 2: 10 minutes
        3: 3600,         # Strike 3: 1 hour
        4: 86400,        # Strike 4: 24 hours
        5: 2592000       # Strike 5+: 30 days
    }
    MAX_STRIKES = 5

    def __init__(self, api_keys: list[str]):
        """
        Initializes the KeyManager.
        
        Args:
            api_keys: A list of API key strings.
        """
        if not api_keys:
            log.critical("KeyManager initialized with no API keys.")
            raise ValueError("API keys list cannot be empty.")
            
        # available_keys is a deque for efficient round-robin (popleft, append)
        self.available_keys = deque(api_keys)
        
        # cooldown_keys stores the key and its epoch release time
        # e.g., {"key-abc": 1678886460}
        self.cooldown_keys = {}
        
        # key_failure_strikes tracks consecutive failures for each key
        # e.g., {"key-abc": 2}
        self.key_failure_strikes = {}
        log.info(f"KeyManager initialized with {len(api_keys)} keys.")

    def _reclaim_keys(self):
        """
        Checks the cooldown pool and moves any released keys back to the 
        available pool. This is called automatically by get_key.
        """
        current_time = time.time()
        released_keys = []
        
        # Find keys ready for release
        for key, release_time in self.cooldown_keys.items():
            if current_time >= release_time:
                released_keys.append(key)
        
        # --- FIX: Shuffle the reclaimed keys ---
        # This prevents the available_keys deque from resetting to the
        # exact same order every time a batch of keys is reclaimed.
        random.shuffle(released_keys)
        # --- END FIX ---
                
        # Move released keys back to available pool
        for key in released_keys:
            del self.cooldown_keys[key]
            # Key has served its penalty, reset strikes and make it available
            self.key_failure_strikes[key] = 0 
            self.available_keys.append(key)
            log.info(f"Key '...{key[-4:]}' reclaimed from cooldown.")
            
    def get_key(self) -> tuple[str | None, float]:
        """
        Gets the next available API key.
        
        Returns:
            A tuple: (key_string, next_available_time_in_seconds)
            If a key is available, returns (key, 0.0)
            If no key is available, returns (None, seconds_until_next_key)
        """
        # Always check for released keys first
        self._reclaim_keys()
        
        if not self.available_keys:
            # All keys are on cooldown. Find the one that will be free the soonest.
            if not self.cooldown_keys:
                log.error("No available keys and no keys in cooldown. Key list was likely empty.")
                return (None, 0.0)
                
            next_release_time = min(self.cooldown_keys.values())
            wait_time = max(0, next_release_time - time.time())
            return (None, wait_time)
            
        # Get the next key in the round-robin
        key = self.available_keys.popleft()
        return (key, 0.0)

    def report_success(self, key: str):
        """
        Reports a successful API call (e.g., HTTP 200).
        Resets the key's strike count and returns it to the available pool.
        """
        # Key is healthy, reset its strike count
        self.key_failure_strikes[key] = 0
        
        # Add it to the back of the line for the next round-robin
        self.available_keys.append(key)
        log.debug(f"Key '...{key[-4:]}' reported success. Strikes reset.")

    def report_failure(self, key: str):
        """
        Reports a failed API call (e.g., HTTP 429).
        Increments the key's strike count and places it in the cooldown pool.
        """
        # Increment strike count
        strikes = self.key_failure_strikes.get(key, 0) + 1
        self.key_failure_strikes[key] = strikes
        
        # Get cooldown duration, defaulting to max if strikes exceed defined periods
        cooldown_duration = self.COOLDOWN_PERIODS.get(
            strikes, 
            self.COOLDOWN_PERIODS[self.MAX_STRIKES]
        )
        
        # Set its release time
        release_time = time.time() + cooldown_duration
        
        # Move key to cooldown pool
        self.cooldown_keys[key] = release_time
        log.warning(f"Key '...{key[-4:]}' reported failure. Strike {strikes}. On cooldown for {cooldown_duration}s.")

    def get_status(self) -> dict:
        """
        Returns the current state of all key pools for display.
        (Useful for debugging in Streamlit)
        """
        current_time = time.time()
        
        cooldown_status = {
            key: {
                "release_in_seconds": max(0, release_time - current_time),
                "strikes": self.key_failure_strikes.get(key, 0)
            }
            for key, release_time in self.cooldown_keys.items()
        }
        
        return {
            "available_keys_count": len(self.available_keys),
            "cooldown_keys_count": len(self.cooldown_keys),
            "available_keys": list(self.available_keys),
            "cooldown_status": cooldown_status
        }