import streamlit as st
import requests
import json
import time
from modules.key_manager import KeyManager
from modules.utils import AppLogger

AVAILABLE_MODELS = [
    "gemini-3-pro-preview",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemma-3-27b-it",
    "gemma-3-12b-it"
]
API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

def call_gemini_with_rotation(
    prompt: str,
    system_prompt: str,
    logger: AppLogger,
    model_name: str,
    key_manager: KeyManager,
    max_retries=5
) -> tuple[str | None, str | None]:

    if not key_manager:
        logger.log("❌ ERROR: KeyManager not initialized.")
        return None, "System Configuration Error"

    for i in range(max_retries):
        current_api_key = None
        key_name = "Unknown"

        try:
            key_name, current_api_key, wait_time = key_manager.get_key(target_model=model_name)

            if not current_api_key:
                logger.log(f"⏳ All keys exhausted for {model_name}. Waiting {wait_time:.0f}s...")
                if wait_time > 0 and i < max_retries - 1:
                    time.sleep(wait_time)
                    continue
                else:
                    return None, f"Global Rate Limit for {model_name}"

            logger.log(f"🔑 Acquired '{key_name}' | Model: {model_name} (Attempt {i+1})")

            gemini_url = f"{API_BASE_URL}/{model_name}:generateContent?key={current_api_key}"

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "generationConfig": {"temperature": 0.5, "maxOutputTokens": 8192}
            }
            headers = {'Content-Type': 'application/json'}

            response = requests.post(gemini_url, headers=headers, data=json.dumps(payload), timeout=90)

            if response.status_code == 200:
                key_manager.report_success(current_api_key, model_id=model_name)
                try:
                    text = response.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                    return text, None
                except (KeyError, IndexError):
                    logger.log(f"⚠️ Invalid JSON response from Google.")
                    key_manager.report_failure(current_api_key, is_server_error=True)
                    continue

            elif response.status_code == 429:
                logger.log(f"⛔ 429 Rate Limit on '{key_name}'. Adding Strike.")
                key_manager.report_failure(current_api_key, is_server_error=False)

            elif response.status_code >= 500:
                logger.log(f"☁️ {response.status_code} Server Error on '{key_name}'. No Penalty.")
                key_manager.report_failure(current_api_key, is_server_error=True)

            else:
                logger.log(f"⚠️ API Error {response.status_code}: {response.text}")
                key_manager.report_failure(current_api_key, is_server_error=True)

        except Exception as e:
            logger.log(f"💥 Exception using '{key_name}': {str(e)}")
            if current_api_key:
                key_manager.report_failure(current_api_key, is_server_error=True)

        if i < max_retries - 1:
            time.sleep(2 ** i)

    return None, "Max Retries Exhausted"
