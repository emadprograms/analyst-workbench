# ai_services.py

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

def call_gemini_api(prompt: str, api_key: str, system_prompt: str, logger: AppLogger, max_retries=5) -> str:
    """
    Calls the Gemini API, handles key switching and retries.
    """
    current_api_key = api_key
    if not API_KEYS or len(API_KEYS) == 0:
        logger.log("Error: No Gemini API keys found in st.secrets.")
        return None
        
    if not current_api_key or current_api_key not in API_KEYS: 
        logger.log("Warning: Provided API key invalid or missing, selecting one at random.")
        current_api_key = random.choice(API_KEYS)
        
    for i in range(max_retries):
        gemini_api_url = f"{API_URL}?key={current_api_key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}], "systemInstruction": {"parts": [{"text": system_prompt}]}}
        headers = {'Content-Type': 'application/json'}
        current_key_index = API_KEYS.index(current_api_key)
        try:
            response = requests.post(gemini_api_url, headers=headers, data=json.dumps(payload), timeout=90)
            if response.status_code in [429, 503]:
                logger.log(f"API Error {response.status_code} on Key #{current_key_index + 1}. Switching...")
                if len(API_KEYS) > 1:
                    new_key_index = random.randint(0, len(API_KEYS) - 1)
                    while new_key_index == current_key_index:
                        new_key_index = random.randint(0, len(API_KEYS) - 1)
                    current_api_key = API_KEYS[new_key_index]
                    logger.log(f"   ...Switched to random Key #{new_key_index + 1}.")
                else: logger.log("   ...Cannot switch (only one key). Retrying same key.")
                delay = 2**i; logger.log(f"   ...Retry in {delay}s..."); time.sleep(delay); continue
            elif response.status_code != 200:
                logger.log(f"API Error {response.status_code}: {response.text} (Key #{current_key_index + 1})")
                if i < max_retries - 1: delay = 2**i; logger.log(f"   ...Retry in {delay}s..."); time.sleep(delay); continue
                else: logger.log("   ...Final fail."); return None
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
            logger.log(f"Invalid API response (Key #{current_key_index + 1}): {json.dumps(result, indent=2)}")
            if i < max_retries - 1: delay = 2**i; logger.log(f"   ...Retry in {delay}s..."); time.sleep(delay); continue
            else: return None
        except requests.exceptions.Timeout:
            logger.log(f"API Timeout (Key #{current_key_index + 1}). Retry {i+1}/{max_retries}...")
            if i < max_retries - 1: time.sleep(2**i)
        except requests.exceptions.RequestException as e:
            logger.log(f"API Request fail: {e} (Key #{current_key_index + 1}). Retry {i+1}/{max_retries}...");
            if i < max_retries - 1: time.sleep(2**i)
    logger.log(f"API failed after {max_retries} retries."); return None

def update_stock_note(ticker_to_update: str, new_raw_text: str, macro_context_summary: str, api_key_to_use: str, logger: AppLogger):
    """
    Updates the main EOD card in the database based on the EOD processor text.
    """
    logger.log(f"--- Starting EOD update for {ticker_to_update} ---")
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
        logger.log("1. Parsing raw summary..."); parsed_data = parse_raw_summary(new_raw_text)
        trade_date = parsed_data.get('date', date.today().isoformat())
        ticker_from_parse = parsed_data.get('ticker')
        if not ticker_from_parse: logger.log(f"Warn: No Ticker parsed for {ticker_to_update}. Using provided.");
        elif ticker_from_parse != ticker_to_update: logger.log(f"Warn: Ticker mismatch ({ticker_from_parse} vs {ticker_to_update}). Using {ticker_from_parse}."); ticker_to_update = ticker_from_parse
        if not ticker_to_update: logger.log("Error: No ticker."); return
        
        logger.log("2. Archiving raw data...");
        archive_columns = ['ticker','date','raw_text_summary','open','high','low','close','poc','vah','val','vwap','orl','orh']
        parsed_data['ticker'] = ticker_to_update
        archive_values = tuple(parsed_data.get(col) for col in archive_columns)
        cursor.execute(f"INSERT OR REPLACE INTO data_archive ({','.join(archive_columns)}) VALUES ({','.join(['?']*len(archive_columns))})", archive_values)
        conn.commit(); logger.log("   ...archived.")
        
        logger.log("3. Fetching Historical Notes & Yesterday's EOD Card...");
        cursor.execute("SELECT historical_level_notes, company_overview_card_json FROM stocks WHERE ticker = ?", (ticker_to_update,))
        company_data = cursor.fetchone(); previous_overview_card_dict={}; historical_notes=""
        if company_data:
            historical_notes = company_data["historical_level_notes"] or ""
            if company_data['company_overview_card_json']:
                try: previous_overview_card_dict = json.loads(company_data['company_overview_card_json']); logger.log("   ...found yesterday's EOD card.")
                except: logger.log(f"   ...Warn: Parse fail yesterday's EOD card."); previous_overview_card_dict = json.loads(DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", ticker_to_update))
            else: logger.log(f"   ...No prior EOD card. Creating new."); previous_overview_card_dict = json.loads(DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", ticker_to_update))
        else:
            logger.log(f"   ...No DB entry. Creating row.");
            try: cursor.execute("INSERT OR IGNORE INTO stocks (ticker) VALUES (?)", (ticker_to_update,)); conn.commit(); logger.log(f"   ...Created row.")
            except Exception as insert_err: logger.log(f"   ...Error creating row: {insert_err}"); return
            previous_overview_card_dict = json.loads(DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", ticker_to_update))

        logger.log("4. Building EOD Note Generator Prompt...");
        note_generator_system_prompt = (
            "You are an expert market structure analyst focused ONLY on participant motivation at MAJOR levels. Maintain 'Company Overview Card' JSON. Get [Historical Notes], [Yesterday's Card], [Today's EOD Action]. Generate NEW EOD card JSON. Prioritize structure unless levels decisively broken. Append `keyAction`. Update plans. Preserve `fundamentalContext`, `sector`, `description`. Output ONLY valid JSON."
        )
        
        prompt = f"""
        [Overall Market Context for Today]
        (Use this to inform the 'why' behind the price action. e.g., if the market was risk-off, a stock holding support is more significant.)
        {macro_context_summary or "No overall market context was provided."}

        [Historical Notes for {ticker_to_update}]
        (CRITICAL STATIC CONTEXT: These define the MAJOR structural levels.)
        {historical_notes}

        [Yesterday's Company Overview Card for {ticker_to_update}] 
        (This defines the ESTABLISHED structure, plans, and the story so far in `keyAction`. Update this cautiously based on MAJOR level interaction.) 
        {json.dumps(previous_overview_card_dict, indent=2)}

        [Today's New Price Action Summary]
        (Objective 5-minute data representing the full completed trading day.)
        {new_raw_text}

        [Your Task for Today: {trade_date} (End of Day Update)]
        Generate the NEW, UPDATED "Company Overview Card" JSON reflecting the completed day's action. Focus on MAJOR level interactions and updating the trade PLANS for TOMORROW.

        **CRITICAL INSTRUCTIONS (LEVELS ARE PARAMOUNT):**
        1.  **PRESERVE STATIC FIELDS:** Copy `fundamentalContext`, `sector`, `companyDescription` **UNCHANGED** from [Yesterday's Card].
        2.  **RESPECT ESTABLISHED STRUCTURE & LEVELS:**
            * **Bias:** Maintain the `bias` from [Yesterday's Card] unless [Today's Action] *decisively breaks AND closes beyond* a MAJOR support/resistance level defined in yesterday's `riskZones` or `historical_level_notes`. Consolidation within the established range does NOT change the bias.
            * **Major S/R:** Keep the MAJOR `support`/`resistance` levels from `historical_level_notes` and [Yesterday's Card] unless today's action *clearly invalidates* them.
            * **Pattern:** Only update `technicalStructure.pattern` if today's action *completes* or *decisively breaks* the pattern.
        3.  **UPDATE `keyAction` (Level-Focused):** APPEND today's action relative to MAJOR levels to the existing `keyAction`.
        4.  **UPDATE PLANS:** Based on the new `keyAction` at MAJOR levels, update BOTH `openingTradePlan` and `alternativePlan` for TOMORROW.
        5.  **UPDATE `volumeMomentum` (Level-Focused):** Describe ONLY how volume confirmed or denied the `keyAction` *at those specific levels*.
        6.  **CALCULATE `confidence` (EOD Logic):**
            * **High:** Today's action *strongly confirmed* the previous bias AND *respected* MAJOR S/R (e.g., bounced from support) **OR** it achieved a *decisive, high-volume CLOSE* beyond a MAJOR S/R level, completing a new structural pattern (e.g., a "Breakout Confirmed" or "Bear Trap Reclaim" like TSLA's $450 reclaim).
            * **Medium:** Today's action was mixed, closed *at* a major level (indecision), or a breakout occurred on low volume.
            * **Low:** Today's action *failed* at a level and *reversed* against the bias (e.g., a "failed breakout" that closed back inside the range, invalidating the structure).
        7.  **Write `screener_briefing` (Top Level)**: A single, compelling sentence summarizing the *most actionable element* for tomorrow's screener.

        [Output Format Constraint]
        Output ONLY the single, complete, updated JSON object. Ensure it is valid JSON. Do not include ```json markdown. """
        
        logger.log(f"5. Calling EOD AI Analyst...");
        ai_response_text = call_gemini_api(prompt, api_key_to_use, note_generator_system_prompt, logger)
        if not ai_response_text: logger.log("Error: No AI response."); return
        
        logger.log("6. Received EOD Card JSON. Parsing & Comparing...");
        json_match = re.search(r"```json\s*([\s\S]+?)\s*```", ai_response_text); ai_response_text = json_match.group(1) if json_match else ai_response_text.strip()
        new_overview_card_dict = None
        try:
            full_parsed_json = json.loads(ai_response_text)
            if isinstance(full_parsed_json, list) and full_parsed_json: new_overview_card_dict = full_parsed_json[0]
            elif isinstance(full_parsed_json, dict): new_overview_card_dict = full_parsed_json
            else: raise json.JSONDecodeError("Not dict/list.", ai_response_text, 0)
        except Exception as e: logger.log(f"Invalid JSON: {e}\n{ai_response_text}"); return
        
        required_keys=['marketNote','confidence','screener_briefing','basicContext','technicalStructure','fundamentalContext','behavioralSentiment','openingTradePlan','alternativePlan']
        required_plan=['planName','knownParticipant','expectedParticipant','trigger','invalidation']; required_alt=required_plan+['scenario']
        missing_keys=[k for k in required_keys if k not in new_overview_card_dict]
        opening_plan_dict=new_overview_card_dict.get('openingTradePlan',{}); alt_plan_dict=new_overview_card_dict.get('alternativePlan',{})
        missing_open=[k for k in required_plan if k not in opening_plan_dict]
        missing_alt=[k for k in required_alt if k not in alt_plan_dict]
        if missing_keys or missing_open or missing_alt: logger.log(f"Missing keys: T({missing_keys}), O({missing_open}), A({missing_alt}). Abort.\n{json.dumps(new_overview_card_dict, indent=2)}"); return
        
        logger.log("   ...JSON parsed & validated.")
        try: 
            diff=DeepDiff(previous_overview_card_dict, new_overview_card_dict, ignore_order=True, view='tree')
            if not diff: logger.log("   ...No changes.")
            else:
                changes_log="   **Changes detected:**\n"; changes_found=False
                if 'values_changed' in diff:
                    changes_log+="| Field | Old | New |\n|---|---|---|\n"; changes_found=True
                    for change in diff['values_changed']:
                        path=change.path().replace("root","").replace("['",".").replace("']","").strip('.'); path=path or"(root)"
                        old=change.t1; new=change.t2; old_s=json.dumps(old,ensure_ascii=False) if isinstance(old,(dict,list)) else str(old); new_s=json.dumps(new,ensure_ascii=False) if isinstance(new,(dict,list)) else str(new)
                        old_s=(old_s[:50]+'...') if len(old_s)>53 else old_s; new_s=(new_s[:50]+'...') if len(new_s)>53 else new_s
                        changes_log+=f"| `{path}` | `{old_s}` | `{new_s}` |\n"
                if not changes_found and ('dictionary_item_added' in diff or 'dictionary_item_removed' in diff): changes_log+="   ...Structural changes only.\n"
                elif changes_found: logger.log(changes_log)
        except Exception as e: logger.log(f"   ...Error comparing: {e}.")
        
        logger.log(f"   ...AI Confidence: `{new_overview_card_dict.get('confidence','N/A')}`")
        logger.log(f"   ...AI Briefing: `{new_overview_card_dict.get('screener_briefing','N/A')}`")
        logger.log(f"   ...AI Plan: `{new_overview_card_dict.get('openingTradePlan',{}).get('planName','N/A')}`")
        
        logger.log("7. Saving NEW EOD Card..."); today_str=date.today().isoformat()
        new_json_str=json.dumps(new_overview_card_dict, indent=2)
        cursor.execute("UPDATE stocks SET company_overview_card_json=?, last_updated=? WHERE ticker=?", (new_json_str, today_str, ticker_to_update))
        if cursor.rowcount==0: logger.log(f"   ...Warn: Update fail {ticker_to_update} (row 0). Init first.")
        else: conn.commit(); logger.log(f"--- Success EOD update {ticker_to_update} ---")
    except Exception as e: logger.log(f"Unexpected error in EOD update: `{e}`")
    finally:
        if conn: conn.close()

def update_economy_card(manual_summary: str, etf_summaries_text: str, api_key_to_use: str, logger: AppLogger):
    """
    Updates the global Economy Card in the database using AI.
    """
    logger.log("--- Starting Economy Card EOD Update ---")
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        logger.log("1. Fetching previous day's Economy Card...")
        cursor.execute("SELECT economy_card_json FROM market_context WHERE context_id = 1")
        eco_data = cursor.fetchone()
        
        previous_economy_card_dict = {}
        if eco_data and eco_data['economy_card_json']:
            try:
                previous_economy_card_dict = json.loads(eco_data['economy_card_json'])
                logger.log("   ...found previous Economy Card.")
            except json.JSONDecodeError:
                logger.log("   ...Warn: Could not parse previous card, starting from default.")
                previous_economy_card_dict = json.loads(DEFAULT_ECONOMY_CARD_JSON)
        else:
            logger.log("   ...No previous card found, starting from default.")
            previous_economy_card_dict = json.loads(DEFAULT_ECONOMY_CARD_JSON)

        logger.log("2. Building Economy Card Update Prompt...")
        
        system_prompt = (
            "You are a macro-economic strategist. Your task is to update the global 'Economy Card' JSON. "
            "You will receive the previous card, a manual summary from the user, and EOD data for key ETFs. "
            "Your primary goal is to synthesize this information into an updated macro view. "
            "CRITICAL: You MUST append to the `marketKeyAction` field to continue the narrative, not replace it. "
            "Output ONLY the single, valid JSON object."
        )

        prompt = f"""
        [CONTEXT & INSTRUCTIONS]
        Your task is to generate the updated "Economy Card" JSON for today, {date.today().isoformat()}.
        Synthesize all the provided information to create a comprehensive macro-economic outlook.

        **CRITICAL RULE: APPEND, DON'T REPLACE.**
        You MUST append today's analysis to the `marketKeyAction` field from the [Previous Day's Card]. Do not erase the existing story. Start a new line with today's date.

        [DATA]
        1.  **Previous Day's Economy Card:**
            (This is the established macro context and narrative.)
            {json.dumps(previous_economy_card_dict, indent=2)}

        2.  **User's Manual Daily Summary:**
            (This is the user's high-level take on the day's events. Give this high importance for the `marketNarrative`.)
            "{manual_summary}"

        3.  **Today's EOD ETF Data Summaries:**
            (This is the objective price and volume data for key market indices and sectors.)
            {etf_summaries_text}

        [YOUR TASK]
        Generate the new, updated "Economy Card" JSON.
        - Update `marketNarrative` and `marketBias` based on all inputs.
        - APPEND to `marketKeyAction`.
        - Update `sectorRotation` and based on the ETF data.
        - Update `keyEconomicEvents` and `marketInternals` if new information is available.
        - **Update `indexAnalysis` for SPY, QQQ, IWM, and DIA** based on their respective EOD data summaries.
        - **Update the `interMarketAnalysis` section.** Analyze the data for assets like TLT (Bonds), GLD (Gold), UUP (Dollar), and BTC (Crypto) to describe the broader capital flow story. Is money flowing to safety (bonds/gold up) or into risk (equities/crypto up)?
        - Output ONLY the single, complete, updated JSON object.
        """

        logger.log("3. Calling Macro Strategist AI...")
        ai_response_text = call_gemini_api(prompt, api_key_to_use, system_prompt, logger)

        if not ai_response_text:
            logger.log("Error: No response from Macro AI. Aborting update.")
            return

        logger.log("4. Parsing and validating new Economy Card...")
        json_match = re.search(r"```json\s*([\s\S]+?)\s*```", ai_response_text)
        ai_response_text = json_match.group(1) if json_match else ai_response_text.strip()

        try:
            new_economy_card_dict = json.loads(ai_response_text)
            if "marketNarrative" not in new_economy_card_dict or "sectorRotation" not in new_economy_card_dict:
                raise ValueError("Validation failed: Key fields missing from AI response.")
            
            logger.log("   ...JSON parsed and validated.")
            logger.log(f"   ...New Market Narrative: {new_economy_card_dict.get('marketNarrative', 'N/A')}")

            logger.log("5. Saving new Economy Card to database...")
            new_json_str = json.dumps(new_economy_card_dict, indent=2)
            cursor.execute("UPDATE market_context SET economy_card_json = ?, last_updated = ? WHERE context_id = 1",
                           (new_json_str, date.today().isoformat()))
            conn.commit()
            logger.log("--- Success: Economy Card EOD update complete! ---")

        except (json.JSONDecodeError, ValueError) as e:
            logger.log(f"Error processing AI response: {e}")
            logger.log_code(ai_response_text, 'text')

    except Exception as e:
        logger.log(f"An unexpected error occurred in update_economy_card: {e}")
    finally:
        if conn:
            conn.close()
