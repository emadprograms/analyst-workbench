import requests
import json
import time
import streamlit as st
import logging

# Import the manager class
from modules.key_manager import KeyManager

# --- Setup Logging ---
# This will log to your Streamlit console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- GLOBAL KEY MANAGER INSTANCE ---
# Load keys from secrets and create ONE instance of the manager.
try:
    if "gemini" not in st.secrets or "api_keys" not in st.secrets["gemini"] or not st.secrets["gemini"]["api_keys"]:
        logger.critical("Keys not found in `.streamlit/secrets.toml`!")
        st.error("Keys not found in `.streamlit/secrets.toml`!")
        KEY_MANAGER = None
    else:
        API_KEYS = st.secrets["gemini"]["api_keys"]
        # Create the single, global instance of the KeyManager
        KEY_MANAGER = KeyManager(API_KEYS)
except Exception as e:
    logger.critical(f"Failed to load keys or initialize KeyManager: {e}")
    KEY_MANAGER = None

# --- Your Refactored API Call Function ---

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"

def call_gemini_api(prompt: str, system_prompt: str, max_retries=5) -> str | None:
    """
    Calls the Gemini API using the stateful KeyManager.
    It automatically handles key rotation, cooldowns, and retries.
    """
    if not KEY_MANAGER:
        logger.error("KeyManager is not initialized. Cannot make API call.")
        return None

    for i in range(max_retries):
        current_api_key = None
        try:
            # 1. Get a key from the manager
            current_api_key, wait_time = KEY_MANAGER.get_key()
            
            if not current_api_key:
                logger.warning(f"All keys are on cooldown. Waiting {wait_time:.0f}s... (Attempt {i+1}/{max_retries})")
                if wait_time > 0 and i < max_retries - 1:
                    time.sleep(wait_time)
                    continue  # Try getting a key again
                else:
                    logger.error("All keys are on cooldown and max retries reached or no wait time.")
                    return None
            
            logger.info(f"Attempt {i+1}/{max_retries} using key '...{current_api_key[-4:]}'")
            
            # --- Make the API Call ---
            gemini_api_url = f"{API_URL}?key={current_api_key}"
            payload = {"contents": [{"parts": [{"text": prompt}]}], "systemInstruction": {"parts": [{"text": system_prompt}]}}
            headers = {'Content-Type': 'application/json'}
            
            response = requests.post(gemini_api_url, headers=headers, data=json.dumps(payload), timeout=90)
            
            # --- AUTOMATICALLY REPORT STATUS ---
            if response.status_code == 200:
                # 2a. Report SUCCESS
                KEY_MANAGER.report_success(current_api_key)
                
                # ... (rest of your JSON parsing logic)
                result = response.json()
                candidates = result.get("candidates")
                if candidates and len(candidates) > 0:
                    content = candidates[0].get("content")
                    if content:
                        parts = content.get("parts")
                        if parts and len(parts) > 0:
                            text_part = parts[0].get("text")
                            if text_part is not None:
                                logger.info(f"API call successful with key '...{current_api_key[-4:]}'")
                                return text_part.strip()
                
                # If parsing fails
                logger.warning(f"Invalid API response structure from key '...{current_api_key[-4:]}'")
                # We don't retry on bad data, but the key was successful
                return None 

            elif response.status_code == 429:
                # 2b. Report FAILURE (429)
                logger.warning(f"API Error 429 (Rate Limit) on key '...{current_api_key[-4:]}'. Reporting failure.")
                KEY_MANAGER.report_failure(current_api_key)
                # Loop will continue and get a new key
            
            else:
                # 2c. Report OTHER Failures (e.g., 400, 500)
                # We'll treat these as temporary failures too
                logger.error(f"API Error {response.status_code}: {response.text} on key '...{current_api_key[-4:]}'. Reporting failure.")
                KEY_MANAGER.report_failure(current_api_key)
                # Loop will continue and get a new key

        except requests.exceptions.Timeout:
            logger.warning(f"API Timeout on key '...{current_api_key[-4:]}'. Reporting failure.")
            if current_api_key:
                KEY_MANAGER.report_failure(current_api_key)
                
        except requests.exceptions.RequestException as e:
            logger.error(f"API Request fail: {e} on key '...{current_api_key[-4:]}'. Reporting failure.")
            if current_api_key:
                KEY_MANAGER.report_failure(current_api_key)
        
        # Exponential backoff *before next retry* if we had a failure
        if i < max_retries - 1:
            delay = 2**i
            logger.info(f"Retrying in {delay}s...")
            time.sleep(delay)

    logger.error(f"API call failed after {max_retries} retries."); 
    return None

# --- Example of how to use it in Streamlit ---
# You can now use this file as a library

if __name__ == "__main__":
    st.title("Test API Caller")
    
    if st.button("Call Gemini API"):
        if not KEY_MANAGER:
            st.error("Key Manager is not initialized. Check secrets.toml")
        else:
            with st.spinner("Calling Gemini API..."):
                prompt = "Tell me a short joke."
                system_prompt = "You are a helpful assistant."
                response = call_gemini_api(prompt, system_prompt)
            
            if response:
                st.success("Success!")
                st.write(response)
            else:
                st.error("API call failed after all retries.")
            
            st.subheader("Key Manager Status")
            st.json(KEY_MANAGER.get_status())