import requests
import json
import re
import time
import random
from datetime import date
from deepdiff import DeepDiff

from modules.config import API_URL, API_KEYS, DEFAULT_COMPANY_OVERVIEW_JSON, DEFAULT_ECONOMY_CARD_JSON, DATABASE_FILE
from modules.data_processing import parse_raw_summary
from modules.ui_components import AppLogger
import sqlite3

# --- FIX #1: MODIFIED call_gemini_api ---
# - Removed the 'api_key' argument from the function signature.
# - The function now picks its own random key for the FIRST attempt.
def call_gemini_api(prompt: str, system_prompt: str, logger: AppLogger, max_retries=5) -> str:
    """
    Calls the Gemini API, handles key switching and retries.
    This function now randomly selects its own key for the first attempt.
    """
    if not API_KEYS or len(API_KEYS) == 0:
        logger.log("Error: No Gemini API keys found in st.secrets.")
        return None
    
    # --- THIS IS THE FIX ---
    # Always select a random key for the *first attempt*.
    current_api_key = random.choice(API_KEYS)
    current_key_index = API_KEYS.index(current_api_key)
    logger.log(f"Selected random Key #{current_key_index + 1} for first attempt.")
    # --- END FIX ---
        
    for i in range(max_retries):
        gemini_api_url = f"{API_URL}?key={current_api_key}"
        # current_key_index is already set
        
        payload = {"contents": [{"parts": [{"text": prompt}]}], "systemInstruction": {"parts": [{"text": system_prompt}]}}
        headers = {'Content-Type': 'application/json'}
        
        try:
            response = requests.post(gemini_api_url, headers=headers, data=json.dumps(payload), timeout=90)
            
            if response.status_code in [429, 503]:
                logger.log(f"API Error {response.status_code} on Key #{current_key_index + 1}. Switching...")
                
                # Retry logic: Pick a *different* random key
                if len(API_KEYS) > 1:
                    new_key_index = random.randint(0, len(API_KEYS) - 1)
                    # Ensure we don't pick the same key that just failed
                    while new_key_index == current_key_index:
                        new_key_index = random.randint(0, len(API_KEYS) - 1)
                    
                    current_api_key = API_KEYS[new_key_index]
                    current_key_index = new_key_index # Update the index for the next loop
                    logger.log(f"   ...Switched to random Key #{current_key_index + 1}.")
                else:
                     logger.log("   ...Cannot switch (only one key). Retrying same key.")
                
                delay = 2**i
                logger.log(f"   ...Retry in {delay}s...")
                time.sleep(delay)
                continue # Go to the next iteration of the `for` loop
            
            elif response.status_code != 200:
                logger.log(f"API Error {response.status_code}: {response.text} (Key #{current_key_index + 1})")
                if i < max_retries - 1:
                    delay = 2**i
                    logger.log(f"   ...Retry in {delay}s...")
                    time.sleep(delay)
                    continue
                else:
                    logger.log("   ...Final fail.")
                    return None

            # --- Success case ---
            result = response.json()
            candidates = result.get("candidates")
            if candidates and len(candidates) > 0:
                content = candidates[0].get("content")
                if content:
                    parts = content.get("parts")
                    if parts and len(parts) > 0:
                        text_part = parts[0].get("text")
                        if text_part is not None:
                            return text_part.strip()
            
            # --- Handle invalid response ---
            logger.log(f"Invalid API response (Key #{current_key_index + 1}): {json.dumps(result, indent=2)}")
            if i < max_retries - 1:
                delay = 2**i
                logger.log(f"   ...Retry in {delay}s...")
                time.sleep(delay)
                continue
            else:
                return None

        except requests.exceptions.Timeout:
            logger.log(f"API Timeout (Key #{current_key_index + 1}). Retry {i+1}/{max_retries}...")
            if i < max_retries - 1: time.sleep(2**i)
        except requests.exceptions.RequestException as e:
            logger.log(f"API Request fail: {e} (Key #{current_key_index + 1}). Retry {i+1}/{max_retries}...");
            if i < max_retries - 1: time.sleep(2**i)

    logger.log(f"API failed after {max_retries} retries."); return None

# --- FIX #2: MODIFIED update_company_card ---
# - Removed 'api_key' argument
# - Added 'previous_card_date' and 'new_eod_date' arguments
def update_company_card(
    ticker: str, 
    previous_card_json: str, 
    previous_card_date: str, # <-- Argument added
    historical_notes: str, 
    new_eod_summary: str, 
    new_eod_date: date, # <-- Argument added
    market_context_summary: str, 
    logger: AppLogger = None
):
    """
    Generates an updated company overview card using AI.
    This function is decoupled from the database and focuses on prompt engineering and AI interaction.
    """
    if logger is None:
        logger = AppLogger(st_container=None) # Dummy logger if none provided

    logger.log(f"--- Starting Company Card AI update for {ticker} ---")

    try:
        previous_overview_card_dict = json.loads(previous_card_json)
        logger.log("1. Parsed previous company card.")
    except (json.JSONDecodeError, TypeError):
        logger.log("   ...Warn: Could not parse previous card. Starting from default.")
        previous_overview_card_dict = json.loads(DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", ticker))

    logger.log("2. Building EOD Note Generator Prompt...")
    note_generator_system_prompt = (
        "You are an expert market structure analyst focused ONLY on participant motivation at MAJOR levels. Maintain 'Company Overview Card' JSON. Get [Historical Notes], [Yesterday's Card], [Today's EOD Action]. Generate NEW EOD card JSON. Prioritize structure unless levels decisively broken. Append `keyAction`. Update plans. Preserve `fundamentalContext`, `sector`, `description`. Output ONLY valid JSON."
    )
    
    # --- THIS IS THE FIX ---
    # It no longer uses date.today(). It uses the passed-in 'new_eod_date'.
    trade_date_str = new_eod_date.isoformat()
    # --- END FIX ---

    prompt = f"""
    [Overall Market Context for Today]
    (Use this to inform the 'why' behind the price action.)
    {market_context_summary or "No overall market context was provided."}

    [Historical Notes for {ticker}]
    (CRITICAL STATIC CONTEXT: These define the MAJOR structural levels.)
    {historical_notes or "No historical notes provided."}

    [Yesterday's Company Overview Card for {ticker} (from {previous_card_date or "N/A"})] 
    (This defines the ESTABLISHED structure and plans. Update this cautiously.) 
    {json.dumps(previous_overview_card_dict, indent=2)}

    [Today's New Price Action Summary (for {trade_date_str})]
    (Objective data representing the completed trading day.)
    {new_eod_summary}

    [Your Task for Today: {trade_date_str} (End of Day Update)]
    Generate the NEW, UPDATED "Company Overview Card" JSON reflecting the completed day's action.

    **CRITICAL INSTRUCTIONS:**
    1.  **PRESERVE STATIC FIELDS:** Copy `fundamentalContext`, `sector`, `companyDescription` **UNCHANGED**.
    2.  **RESPECT ESTABLISHED STRUCTURE:** Maintain the `bias` unless today's action *decisively breaks AND closes beyond* a MAJOR support/resistance level.
    3.  **UPDATE `keyAction`:** This is a running log. You **MUST NOT** summarize or rewrite the existing text. You **MUST** append a new line starting with today's date. Example: '...existing story...\n{trade_date_str}: Today buyers...'.
    4.  **UPDATE PLANS:** Based on the new `keyAction`, update BOTH `openingTradePlan` and `alternativePlan` for TOMORROW.
    5.  **CALCULATE `confidence`:** High (strong confirmation), Medium (mixed signals), Low (reversal/failure).
    6.  **Write `screener_briefing`:** A single, compelling sentence summarizing the most actionable element.
    7.  **UPDATE `tickerDate`:** Set the `basicContext.tickerDate` field to "{ticker} | {trade_date_str}".

    [Output Format Constraint]
    Output ONLY the single, complete, updated JSON object. Ensure it is valid JSON. Do not include ```json markdown. """
    
    logger.log(f"3. Calling EOD AI Analyst for {ticker}...");
    
    # --- THIS IS THE FIX ---
    # Removed the line that picked API_KEYS[0]
    # We now call call_gemini_api without an api_key, letting it pick.
    ai_response_text = call_gemini_api(prompt, note_generator_system_prompt, logger)
    # --- END FIX ---

    if not ai_response_text: 
        logger.log(f"Error: No AI response for {ticker}."); 
        return None
    
    logger.log(f"4. Received EOD Card for {ticker}. Parsing & Validating...")
    json_match = re.search(r"```json\s*([\s\S]+?)\s*```", ai_response_text)
    ai_response_text = json_match.group(1) if json_match else ai_response_text.strip()
    
    try:
        # Validate the JSON structure before returning
        new_overview_card_dict = json.loads(ai_response_text)
        required_keys=['marketNote','confidence','screener_briefing','basicContext','technicalStructure','fundamentalContext','behavioralSentiment','openingTradePlan','alternativePlan']
        if any(k not in new_overview_card_dict for k in required_keys):
            logger.log(f"Error: AI response for {ticker} is missing required keys.")
            return None
        
        logger.log(f"--- Success: AI update for {ticker} complete. ---")
        return ai_response_text

    except json.JSONDecodeError as e:
        logger.log(f"Error: Failed to decode AI response JSON for {ticker}. Details: {e}")
        logger.log_code(ai_response_text, language='text')
        return None
    except Exception as e:
        logger.log(f"Unexpected error validating AI response for {ticker}: {e}")
        return None

# --- FIX #3: MODIFIED update_economy_card ---
# - Removed 'api_key' argument
# - Added 'selected_date' argument
def update_economy_card(
    current_economy_card: str, 
    daily_market_news: str, 
    etf_summaries: str, 
    selected_date: date, # <-- Argument added
    logger: AppLogger = None
):
    """
    Updates the global Economy Card in the database using AI.
    """
    if logger is None:
        logger = AppLogger(st_container=None) # Create a dummy logger if none is provided
    
    logger.log("--- Starting Economy Card EOD Update ---")

    try:
        previous_economy_card_dict = json.loads(current_economy_card)
    except (json.JSONDecodeError, TypeError):
        logger.log("   ...Warn: Could not parse previous card, starting from default.")
        previous_economy_card_dict = json.loads(DEFAULT_ECONOMY_CARD_JSON)

    logger.log("2. Building Economy Card Update Prompt...")
    
    system_prompt = (
        "You are a macro-economic strategist. Your task is to update the global 'Economy Card' JSON. "
        "You will receive the previous card, a manual summary from the user, and EOD data for key ETFs. "
        "Your primary goal is to synthesize this information into an updated macro view. "
        "CRITICAL: You MUST append to the `marketKeyAction` field to continue the narrative, not replace it. "
        "Output ONLY the single, valid JSON object."
    )
    
    # --- THIS IS THE FIX ---
    # It no longer uses date.today(). It uses the passed-in 'selected_date'.
    trade_date_str = selected_date.isoformat()
    # --- END FIX ---

    prompt = f"""
    [CONTEXT & INSTRUCTIONS]
    Your task is to generate the updated "Economy Card" JSON for {trade_date_str}.
    Synthesize all the provided information to create a comprehensive macro-economic outlook.
    
    [Previous Day's Economy Card]
    (This is the established macro context. Update it based on the new information.)
    {json.dumps(previous_economy_card_dict, indent=2)}

    [User's Manual Summary for {trade_date_str}]
    (This is a high-signal, qualitative input from the analyst.)
    {daily_market_news or "No manual summary provided."}

    [Key ETF Summaries for {trade_date_str}]
    (This is the objective price action from key market-wide ETFs.)
    {etf_summaries or "No ETF summaries provided."}

    **CRITICAL INSTRUCTIONS:**
    1.  **UPDATE `marketBias`:** Based on the synthesis of all inputs, determine if the overall market bias is `Bullish`, `Bearish`, or `Neutral`.
    2.  **UPDATE `marketNarrative`:** Write a new, concise narrative explaining the *reasoning* for the updated bias.
    3.  **APPEND `marketKeyAction`:** This is a running log. You **MUST NOT** summarize or rewrite the existing text. You **MUST** append a new line starting with today's date. Example: '...existing story...\n{trade_date_str}: Fed meeting...'.
    4.  **UPDATE `keyEconomicEvents`:** Update the `last_24h` and `next_24h` fields based on the new data for {trade_date_str}.
    5.  **PRESERVE `keyLevels`:** The `keyLevels` for major indices should remain static unless a truly major, multi-year level is broken.

    [Output Format Constraint]
    Output ONLY the single, complete, updated JSON object. Ensure it is valid JSON. Do not include ```json markdown.
    """

    logger.log("3. Calling Macro Strategist AI...")
    
    # --- THIS IS THE FIX ---
    # Removed the line that picked API_KEYS[0]
    # We now call call_gemini_api without an api_key, letting it pick.
    ai_response_text = call_gemini_api(prompt, system_prompt, logger)
    # --- END FIX ---
    
    if not ai_response_text:
        logger.log("Error: No response from AI for economy card update.")
        return None

    logger.log("4. Received new Economy Card. Parsing and validating...")
    json_match = re.search(r"```json\s*([\s\S]+?)\s*```", ai_response_text)
    if json_match:
        ai_response_text = json_match.group(1)
    
    try:
        # Basic validation before returning
        json.loads(ai_response_text)
        logger.log("--- Success: Economy Card generation complete! ---")
        return ai_response_text
    except json.JSONDecodeError as e:
        logger.log(f"Error: Failed to decode AI response for economy card. Details: {e}")
        logger.log_code(ai_response_text, language='text')
        return None
    except Exception as e:
        logger.log(f"An unexpected error occurred during economy card update: {e}")
        return None