from __future__ import annotations

import requests
import json
import re
import time
import logging
from datetime import date
from deepdiff import DeepDiff

# --- Core Module Imports ---
# 1. FIX: Removed API_KEYS. 
# 2. KEY_MANAGER is initialized locally now
from modules.core.config import (
    API_BASE_URL, 
    MODEL_NAME,
    DEFAULT_COMPANY_OVERVIEW_JSON, 
    DEFAULT_ECONOMY_CARD_JSON,
    TURSO_DB_URL, # Imported
    TURSO_AUTH_TOKEN # Imported
)
from modules.core.key_manager import KeyManager # <-- Imported Class
# 3. FIX: Removed missing data processing module import
from modules.core.logger import AppLogger
from modules.data.db_utils import get_db_connection
from modules.analysis.impact_engine import get_or_compute_context
from modules.core.tracker import ExecutionTracker
from modules.ai.quality_validators import validate_company_card, validate_economy_card

# --- GLOBAL TRACKER ---
TRACKER = ExecutionTracker()

# --- GLOBAL KEY MANAGER INITIALIZATION ---
# This breaks the circular dependency with config.py
try:
    if "KEY_MANAGER" not in globals():
        KEY_MANAGER = KeyManager(db_url=TURSO_DB_URL, auth_token=TURSO_AUTH_TOKEN)
        # KEY_MANAGER.init_keys_from_env()  <-- Removed, handled by __init__ or DB
        logging.info("✅ KeyManager initialized successfully (in ai_services).")
except Exception as e:
    logging.critical(f"CRITICAL: Failed to initialize KeyManager: {e}")
    KEY_MANAGER = None


# ---------------------------------------------------------------------------
# JSON PARSING UTILITIES
# ---------------------------------------------------------------------------

def _safe_parse_ai_json(text: str) -> dict | None:
    r"""
    Robustly parses an AI response string into a Python dict.

    Handles three cases in priority order:

    1. Direct JSON string -- the common path when using structured-output mode
       (``responseMimeType: application/json``).
    2. A triple-backtick fenced code block -- the AI sometimes wraps its output
       in fences even with structured outputs requested.  We search for the
       **last** fenced block so that stray examples inside the prompt don't
       accidentally match.
    3. First bare ``{...}`` object found anywhere in the text -- last-resort
       extraction when fences are absent but the JSON is embedded in prose.

    Returns ``None`` (not an empty dict) if parsing fails at every level so that
    callers can distinguish "nothing usable" from "an empty object".
    """
    if not text or not isinstance(text, str):
        return None

    stripped = text.strip()

    # --- Case 1: direct JSON ---
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # --- Case 2: last ```json … ``` block ---
    fenced_blocks = re.findall(r"```(?:json)?\s*([\s\S]+?)\s*```", stripped)
    for candidate in reversed(fenced_blocks):  # prefer the last / outermost block
        try:
            return json.loads(candidate.strip())
        except json.JSONDecodeError:
            continue

    # --- Case 3: first bare {...} object ---
    brace_match = re.search(r"\{[\s\S]+\}", stripped)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    return None

# --- The Robust API Caller (V8) ---
def call_gemini_api(prompt: str, system_prompt: str, logger: AppLogger, model_name: str, max_retries=5, **kwargs) -> str | None:
    """
    Calls Gemini API using dynamic model selection and quota management.
    """
    if not KEY_MANAGER:
        logger.log("❌ ERROR: KeyManager not initialized.")
        return None
    
    # Estimate tokens for quota check
    est_tok = KEY_MANAGER.estimate_tokens(prompt + system_prompt)
    logger.log(f"📝 Request Size Estimate: ~{est_tok} tokens")

    for i in range(max_retries):
        current_api_key = None
        key_name = "Unknown"

        try:
            # 1. ACQUIRE: Request key specifically for this model's bucket
            # Returns: (key_name, key_value, wait_time, real_model_id)
            key_name, current_api_key, wait_time, real_model_id = KEY_MANAGER.get_key(config_id=model_name, estimated_tokens=est_tok)
            
            if not current_api_key:
                if wait_time == -1.0:
                    logger.log(f"❌ FATAL: Prompt too large for {model_name} limits.")
                    TRACKER.log_call(est_tok, False, model_name, ticker=kwargs.get("tracker_ticker"), error="Prompt too large")
                    return None
                
                logger.log(f"⏳ All keys exhausted for {model_name}. Waiting {wait_time:.0f}s... (Attempt {i+1})")
                if wait_time > 0 and i < max_retries - 1:
                    time.sleep(wait_time)
                    continue
                else:
                    logger.log(f"❌ ERROR: Global rate limit reached for {model_name}.")
                    TRACKER.log_call(0, False, model_name, ticker=kwargs.get("tracker_ticker"), error="Global Rate Limit")
                    return None
            
            logger.log(f"🔑 Acquired '{key_name}' | Model: {model_name} (Attempt {i+1})")
            
            # 2. USE: Construct Dynamic URL using the internal model ID
            gemini_url = f"{API_BASE_URL}/{real_model_id}:generateContent?key={current_api_key}"
            
            payload = {
                "contents": [{"parts": [{"text": prompt}]}], 
                "systemInstruction": {"parts": [{"text": system_prompt}]}
            }
            
            # --- NEW: Inject schema if provided (Structured Outputs) ---
            if "response_schema" in kwargs:
                payload["generationConfig"] = {
                    "responseMimeType": "application/json",
                    "responseSchema": kwargs["response_schema"]
                }
                
            headers = {'Content-Type': 'application/json'}
            
            response = requests.post(gemini_url, headers=headers, data=json.dumps(payload), timeout=120)
            
            # 3. REPORT: Pass internal model_id for correct counter increment
            if response.status_code == 200:
                result = response.json()
                
                # V8 FIX: Use REAL usage data if available
                usage_meta = result.get("usageMetadata", {})
                real_tokens = usage_meta.get("totalTokenCount", est_tok) # fallback to estimate
                
                # Log the correction if significant
                if real_tokens > est_tok * 1.2:
                    logger.log(f"   ...Usage Correction: Est {est_tok} -> Real {real_tokens}")
                    
                KEY_MANAGER.report_usage(current_api_key, tokens=real_tokens, model_id=real_model_id)
                TRACKER.log_call(real_tokens, True, model_name, ticker=kwargs.get("tracker_ticker"))

                try:
                    return result["candidates"][0]["content"]["parts"][0]["text"].strip()
                except (KeyError, IndexError):
                    logger.log(f"⚠️ Invalid JSON Structure: {result}")
                    KEY_MANAGER.report_failure(current_api_key, is_info_error=True)
                    continue 

            elif response.status_code == 429:
                err_text = response.text
                if "limit: 0" in err_text or "Quota exceeded" in err_text:
                    logger.log(f"⛔ BILLING ISSUE on '{key_name}'. Google says Quota is 0.")
                    logger.log(f"   ACTION: Go to Google Cloud Console -> Billing -> Link a Card to project.")
                    KEY_MANAGER.report_failure(current_api_key, is_info_error=False) 
                else:
                    logger.log(f"⛔ 429 Rate Limit on '{key_name}'. Triggering 60s Cooldown.")
                    logger.log(f"   Details: {err_text}")
                    KEY_MANAGER.report_failure(current_api_key, is_info_error=False)
            elif response.status_code >= 500:
                logger.log(f"☁️ {response.status_code} Server Error. Waiting 10s...")
                KEY_MANAGER.report_failure(current_api_key, is_info_error=True)
                time.sleep(10) # Give the server breathing room
            else:
                err_text = response.text
                logger.log(f"⚠️ API Error {response.status_code}: {err_text}")
                # Permanently retire expired/invalid keys
                if response.status_code == 400 and ("API_KEY_INVALID" in err_text or "API key expired" in err_text):
                    logger.log(f"   🗑️ Retiring expired key '{key_name}' permanently.")
                    KEY_MANAGER.report_fatal_error(current_api_key)
                else:
                    KEY_MANAGER.report_failure(current_api_key, is_info_error=True)

        except requests.exceptions.ReadTimeout:
            logger.log(f"💥 Timeout: Request timed out for '{key_name}'. Key goes to cooldown.")
            if current_api_key:
                # Timeout means Google likely received & counted the tokens.
                # Treat as a real failure so the key gets a cooldown period.
                KEY_MANAGER.report_failure(current_api_key, is_info_error=False)
        except Exception as e:
            logger.log(f"💥 Exception: {str(e)}")
            if current_api_key:
                KEY_MANAGER.report_failure(current_api_key, is_info_error=True)
        
        if i < max_retries - 1:
            time.sleep(2 ** i)

    logger.log("❌ FATAL: Max retries exhausted.")
    TRACKER.log_call(0, False, model_name, ticker=kwargs.get("tracker_ticker"), error="Max Retries Exhausted")
    return None
    

# --- REFACTORED: update_company_card (PROMPT IS GOOD) ---
def update_company_card(
    ticker: str, 
    previous_card_json: str, 
    previous_card_date: str, 
    historical_notes: str, 
    new_eod_date: date, 
    model_name:str,
    market_context_summary: str, 
    logger: AppLogger = None
):
    """
    Generates an updated company overview card using AI.
    --- MERGED: This function now uses the new, safe architecture
    but with the old, detailed analytical guidance. ---
    """
    if logger is None:
        logger = AppLogger() 

    logger.log(f"--- Starting Company Card AI update for {ticker} ---")

    try:
        previous_overview_card_dict = json.loads(previous_card_json)
        logger.log("1. Parsed previous company card.")
    except (json.JSONDecodeError, TypeError):
        logger.log("   ...Warn: Could not parse previous card. Starting from default.")
        previous_overview_card_dict = json.loads(DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", ticker))

    # --- Extract the keyActionLog from the previous card ---
    previous_action_log = previous_overview_card_dict.get("technicalStructure", {}).get("keyActionLog", [])
    if isinstance(previous_action_log, list):
         # Get the last 5 entries to keep the prompt context reasonable
        recent_log_entries = previous_action_log[-5:]
    else:
        recent_log_entries = [] # Handle corrupted data

    logger.log("2. Building EOD Note Generator Prompt...")
    
    # --- FINAL System Prompt ---
    system_prompt = (
        "You are an expert market structure analyst. Your *only* job is to apply the specific 4-Participant Trading Model provided below. "
        "Your logic must *strictly* follow this model. You will be given a 'Masterclass' that defines the model's philosophy. "
        "Your job has **four** distinct analytical tasks: "
        "1. **Analyze `behavioralSentiment` (The 'Micro'):** You MUST provide a full 'Proof of Reasoning' for the `emotionalTone` field. "
        "2. **Analyze `technicalStructure` (The 'Macro'):** Use *repeated* participant behavior to define and evolve the *key structural zones*. "
        "3. **Calculate `confidence` (The 'Story'):** You MUST combine the lagging 'Trend_Bias' with the 'Story_Confidence' (H/M/L) and provide a full justification. "
        "4. **Calculate `screener_briefing` (The 'Tactic'):** You MUST synthesize your *entire* analysis to calculate a *new, separate, actionable* 'Setup_Bias' and assemble the final Python-readable data packet. "
        "Do not use any of your own default logic. Your sole purpose is to be a processor for the user's provided framework."
    )

    
    trade_date_str = new_eod_date.isoformat()

    # --- FINAL Main 'Masterclass' Prompt ---
    # --- IMPACT ENGINE INTEGRATION ---
    impact_context_json = "No Data Available"
    
    conn = get_db_connection()
    if conn:
        try:
            # --- CACHING IMPLEMENTED VIA get_or_compute_context ---
            context_card = get_or_compute_context(conn, ticker, trade_date_str, logger)
            impact_context_json = json.dumps(context_card, indent=2)
            logger.log(f"✅ Loaded Impact Context Card for {ticker}")
        except Exception as e:
            logger.log(f"⚠️ Impact Engine Failed for {ticker}: {e}")
            impact_context_json = f"Error generating context: {e}"
        finally:
            conn.close()
    else:
        logger.log("⚠️ DB Connection Failed - Skipping Impact Engine")

    # --- FINAL Main 'Masterclass' Prompt ---
    prompt = f"""
    [Raw Market Context for Today]
    (This contains RAW, unstructured news headlines and snippets from various sources. You must synthesize the macro "Headwind" or "Tailwind" yourself from this data. It also contains company-specific news.)
    <market_context>
    {market_context_summary or "No raw market news was provided."}
    </market_context>

    [Historical Notes for {ticker}]
    (CRITICAL STATIC CONTEXT: These are the MAJOR structural levels. LEVELS ARE PARAMOUNT.)
    <historical_notes ticker="{ticker}">
    {historical_notes or "No historical notes provided."}
    </historical_notes>
    
    [Previous Card (Read-Only)]
    (This is established structure, plans, and `keyActionLog` so far. Read this for the 3-5 day context AND to find the previous 'recentCatalyst' and 'fundamentalContext' data.) 
    <previous_card>
    {json.dumps(previous_overview_card_dict, indent=2)}
    </previous_card>

    [Log of Recent Key Actions (Read-Only)]
    (This is the day-by-day story so far. Use this for context.)
    <recent_key_actions>
    {json.dumps(recent_log_entries, indent=2)}
    </recent_key_actions>

    [Today's New Price Action Summary (IMPACT CONTEXT CARD)]
    (Use this structured 'Value Migration Log' and 'Impact Levels' to determine the 'Nature' of the session.)
    <today_price_action_summary>
    {impact_context_json}
    </today_price_action_summary>

    [Your Task for {trade_date_str}]
    Your task is to populate the JSON template below. You MUST use the following trading model to generate your analysis.

    --- START MASTERCLASS: THE 4-PARTICIPANT MODEL ---

    **Part 1: The Core Philosophy (Exhaustion & Absence)**
    This is the most important concept. Price moves are driven by the *absence* or *exhaustion* of one side, not just the *presence* of the other.
    * **Price falls because:** Committed Buyers are **absent** (they are competing for a better, lower price).
    * **Price rises because:** Committed Sellers are **absent** or **exhausted** (they have finished selling at a level).

    **Part 2: The Two Market States (Stable vs. Unstable)**
    * **1. Stable Market:** (Default) Driven by **Committed Participants**. A rational market focused on "exhaustion" at key levels.
    * **2. Unstable Market:** (Exception) Driven by **Desperate Participants**. An emotional market, a *reaction* to a catalyst (news, panic, FOMO).

    **Part 3: The Four Participant Types**
    * **Committed Buyers:** Patiently accumulate at or below support.
    * **Committed Sellers:** Patiently distribute at or above resistance.
    * **Desperate Buyers:** (FOMO / Panic) Buy *aggressively* at *any* price.
    * **Desperate Sellers:** (Panic / Capitulation) Sell *aggressively* at *any* price.

    **Part 4: The 5 Key Patterns (How to Identify the State)**
    1.  **Accumulation (Stable):** A *slow* fight at support, marked by **higher lows** as sellers become exhausted.
    2.  **Capitulation (Unstable):** A *fast* vacuum, as **Desperate Sellers** sell and **Committed Buyers step away**.
    3.  **Stable Uptrend (Stable):** Caused by **Absent/Exhausted Committed Sellers** at resistance, often followed by a "check" (retest) of the broken level.
    4.  **Washout & Reclaim (Hybrid -> Unstable):** **Committed Buyers** let support break, then turn into **Desperate Buyers** to get filled, causing a *violent reversal*.
    5.  **Chop (Stable):** Equilibrium. **Committed Buyers** defend the low, **Committed Sellers** defend the high. No one is desperate.

    **Part 5: The 3 Levels of Story Confidence (The "Conviction" Score)**
    This is your *separate* analysis of the day's *objective outcome*.
    * **High Story_Confidence:** Today's action was **decisive and confirming**. It *either* 1) strongly *confirmed* the `Trend_Bias` AND *respected* a MAJOR S/R level, *or* 2) it achieved a *decisive, high-volume CLOSE* *beyond* a MAJOR S/R level.
    * **Medium Story_Confidence:** Today's action was **mixed or indecisive**. This includes 1) closing *at* or *near* a major level, 2) a breakout/breakdown on *low, unconvincing volume*, or 3) a "Doji" or "inside day".
    * **Low Story_Confidence:** Today's action was a **failure or reversal**. It *failed* at a key level and *reversed* *against* the `Trend_Bias` (e.g., a "failed breakout" that closed back inside the range).

    --- END MASTERCLASS ---

    **YOUR EXECUTION TASK (Filling the JSON):**

    **1. Calculate `Trend_Bias`:**
        * First, determine the **lagging, multi-day `Trend_Bias`** using the rule: "Maintain the `bias` from the [Previous Card] unless [Today's Action] *decisively breaks AND closes beyond* a MAJOR level."

    **2. `confidence` (The "Story"):**
        * This is your *first* output field. You MUST combine the `Trend_Bias` (from Step 1) with the `Story_Confidence` (from Masterclass Part 5) and provide a "Proof of Reasoning."
        * **Final Format:** "Trend_Bias: [Your calculated Trend_Bias] (Story_Confidence: [High/Medium/Low]) - Reasoning: [Your justification for the H/M/L rating]."
        * **Example:** "Trend_Bias: Bearish (Story_Confidence: Low) - Reasoning: The action was a *failure* against the Bearish trend. It *failed* at $265 resistance and reversed, but the 'Accumulation' pattern means the breakdown itself has failed, matching the 'Low Confidence' definition."

    **3. `basicContext.recentCatalyst` (The "Governing Narrative"):**
        * Manage this as the **cumulative story**.
        * **Step 1:** Read the `recentCatalyst` from the `[Previous Card]`.
        * **Step 2:** *Hunt* the `[Overall Market Context for Today]` for any *company-specific* news.
        * **Step 3 (Execute):**
            * **If new info:** **Append** it to the previous narrative.
            * **If no new info:** **Carry over** the *entire, unchanged* narrative from the `[Previous Card]`.

    **4. `fundamentalContext` (Dynamic Fields):**
        * **`valuation`:** "AI RULE: READ-ONLY".
        * **`analystSentiment` & `insiderActivity`:**
            * **Step 1:** Read from `[Previous Card]`.
            * **Step 2:** *Hunt* the `[Overall Market Context for Today]` for new analyst ratings or insider transactions.
            * **Step 3 (Execute):** **Update** if new info is found, otherwise **carry over** the unchanged data.

    **5. `technicalStructure` Section (The "Macro" / Zone Analysis):**
        * **`majorSupport` / `majorResistance`:**
            * Your base is `[Historical Notes]`.
            * You MUST *evolve* these fields based on *repeated* participant action from the `[Log of Recent Key Actions]`.
            * **Rule:** If 'Committed Buyers' defend a *new* level for 2-3 days, you MUST add it as a 'New tactical support'.
            * **Rule:** If a `Historical Note` level is broken and *held* for 2-3 days, you MUST re-label it (e.g., '$265 (Old Resistance, now 'Stable Market' support)').
        * **`pattern` (The "Structural Narrative"):**
            * This is the **multi-day structural story** *relative to the zones*.
            * (e.g., "Price is in a 'Balance (Chop)' pattern, coiling between the Committed Buyer zone at $415 and the Committed Seller zone at $420.")

    **6. `technicalStructure.volumeMomentum` (The "Volume Analysis"):**
        * **This is your next analysis.** Your job is to be the volume analyst.
        * Describe ONLY how volume from `[Today's New Price Action Summary]` *confirmed or denied* the action *at the specific levels*, explicitly using the 'volume_profile' (POC, VAH, VAL) and 'key_volume_events'.
        * **Example 1 (Confirmation):** "High-volume defense. The rejection of the $239.15 low was confirmed by the day's highest volume spike (key event) and the Value Area Low (VAL), proving Committed Buyers were present in force."
        * **Example 2 (No Confirmation):** "Low-volume breakout. The move above $420 resistance occurred far from the Volume POC on unconvincing volume, signaling a 'Stable Market' (Committed Seller) exhaustion, not 'Unstable' (Desperate Buyer) panic."

    **7. `behavioralSentiment` Section (The "Micro" / Today's Analysis):**
        * **`emotionalTone` (The 3-Act Pattern + Proof of Reasoning):**
            * This is your **Justification**, not a description. You MUST show your work by analyzing the **3-Part Session Arc** (`Pre-Market` -> `RTH` -> `Post-Market`):
            * **1. Act I (Intent):** What did `sessions.pre_market` try to do? (e.g., "Bulls attempted a gap up...").
            * **2. Act II (The Conflict - RTH):** Did `sessions.regular_hours` validate or invalidate that intent? Analyze the 'Value Migration'. (e.g., "...but RTH invalidated the gap immediately, migrating value LOWER on high volume.").
            * **3. Act III (Resolution):** How did `sessions.post_market` close? (e.g., "Weak close near lows confirms rejection.").
            * **Then, label the psychological event.**
            * **Final Format:** "Label - Reasoning: [Your full 3-Act proof]"
            * **Example:** "Accumulation (Stable) - Reasoning: **(Act I)** Pre-market held support. **(Act II)** RTH confirmed this by defending the low and migrating value higher into a 'Wide Expansion' range. **(Act III)** Post-market held gains. This consistency signals **Committed Buyers** are in control."
        * **`newsReaction` (The Surprise / Correlation Analysis):**
            * **You MUST detect the 'Disconnect':** Compare the **Pre-Market News Theme** vs. the **RTH Price Response**.
            * **Scenario A (Validation):** News was Bad -> Price Sold Off. (Standard Headwind).
            * **Scenario B (Surprise/Invalidation - CRITICAL):** News was Bad (e.g., 'Sell America' theme in Pre-Market) -> **Price IGNORED it and Rallied** (RTH). 
            * **Rule:** If price *invalidates* the news theme, you MUST label this as a **MAJOR SIGNAL** of underlying conviction. (e.g., "Bullish Surprise - Stock ignored the 'Sell America' pre-market theme and rallied, proving extreme relative strength.").
        * **`buyerVsSeller` (The Conclusion):**
            * This is your *final synthesis* of the `emotionalTone` and `newsReaction`.
            * (e.g., "Committed Buyers are in firm control. They not only showed a 'Stable Accumulation' pattern at $415 but did so *against* a weak, bearish market, confirming their high conviction.")

    **8. `keyActionLog` / `todaysAction` (STRICT FORMAT — MAX 3 SENTENCES):**
        * This is a **concise daily log entry**, NOT a card summary. It must capture ONLY the day's story arc in 2-3 sentences.
        * **CRITICAL CONSTRAINT:** The `todaysAction` field must be **under 2500 characters**. If your output exceeds this, you have failed the task. Do NOT repeat information from other fields. Do NOT include price levels, S/R zones, plan details, screener data, volume stats, or any content that belongs in other card fields.
        * **Required Format:** `"{trade_date_str}: [Pattern Label] ([Market State]). [1-2 sentences describing the 3-Act session arc using 4-Participant language: who acted, what they did at which key level, and the outcome]."`
        * **GOOD Example:** `"2026-02-13: Accumulation (Stable). Following yesterday's capitulation, the market opened with a gap down but immediately found Committed Buyers defending the major $255 structural POC. Despite a softer broad market, buyers established a series of higher lows throughout RTH, migrating value lower but holding the $255 floor. A high-volume stabilization in post-market confirms seller exhaustion and a tactical stalemate at support."`
        * **BAD Example (DO NOT DO THIS):** Repeating the entire card's analysis, including all support/resistance levels, plan details, screener briefing content, volume profile numbers, catalyst text, or any content from other fields. If your todaysAction reads like a paragraph that could replace the entire card, it is WRONG.
        * Write this field LAST, after all other analysis is complete. Distill, do not duplicate.
    **9. `openingTradePlan` & `alternativePlan`:** Update these for TOMORROW.

    **10. `screener_briefing` (The "Data Packet" for Python):**
        * This is your **final** task. You will generate the data packet *after* all other analysis is complete.
        * **Step 1: Calculate the `Setup_Bias` (Master Synthesis Rule):**
            * Your `Setup_Bias` for *this field only* MUST be a *synthesis* of your `pattern` (Macro) and `emotionalTone` (Micro) findings.
            * **Rule 1 (Change of Character):** If today's `emotionalTone` (e.g., 'Accumulation') *contradicts* the `Trend_Bias` (e.g., 'Bearish'), the **`emotionalTone` takes precedence.** The `Setup_Bias` *must* reflect this *new change* in market character.
                * *(Example: `emotionalTone: 'Accumulation'` at support MUST result in a `Setup_Bias: Neutral` or `Neutral (Bullish Lean)`.)*
            * **Rule 2 (Use Relative Strength):** Use your `newsReaction` (relative strength/weakness) to "shade" the bias.
                * *(Example: `emotionalTone: 'Accumulation'` + `newsReaction: 'Extreme Relative Strength'` = `Setup_Bias: Neutral (Bullish Lean)` or `Bullish`.)*
        * **Step 2: Summarize the `Catalyst`:**
            * Create a clean, one-line summary of the "Governing Narrative" you already built for the `recentCatalyst` field.
            * **Example:** "Post-earnings consolidation and new AI deal."
        * **Step 3: Assemble the "Data Packet":**
            * You *must* output a multi-line string in the *exact* key-value format specified below.
            * For `Plan_A_Level` and `Plan_B_Level`, extract the *primary* price level from the `trigger`.
            * For `S_Levels` and `R_Levels`, extract *all* numerical price levels from `technicalStructure.majorSupport` and `technicalStructure.majorResistance`. Format them as a comma-separated list *inside brackets*.
        * **Exact Output Format:**
        Setup_Bias: [Your *newly calculated* 'Setup Bias' from Step 1]
        Justification: [Your 'Proof of Reasoning' for the Setup_Bias, e.g., "Today's 'Accumulation' by 'Committed Buyers' (40% weight) contradicts the multi-day 'Breakdown' (60% weight), signaling seller exhaustion and forcing a 'Neutral' bias."]
        Catalyst: [Your new *one-line summary* of the 'Governing Narrative']
        Pattern: [Your 'Structural Narrative' from technicalStructure.pattern]
        Plan_A: [The 'planName' from openingTradePlan]
        Plan_A_Level: [Extracted level from Plan A's trigger]
        Plan_B: [The 'planName' from alternativePlan]
        Plan_B_Level: [Extracted level from Plan B's trigger]
        S_Levels: [Your extracted list of support levels, e.g., $266.25, $264.00]
        R_Levels: [Your extracted list of resistance levels, e.g., $271.41, $275.00]

    **CRITICAL ANALYTICAL RULES (LEVELS ARE PARAMOUNT):**
    * **Bias:** (This rule is *only* for the `Trend_Bias` calculation in Task 1. Do not use it for the `Setup_Bias` in Task 10.) Maintain the `bias` from the [Previous Card] unless [Today's Action] *decisively breaks AND closes beyond* a MAJOR level.
    * **Plans:** Update BOTH `openingTradePlan` and `alternativePlan` for TOMORROW.
    * **Volume:** (This rule is now handled in Task 6).

    [Output Format Constraint]
    Output ONLY a single, valid JSON object in this exact format. **You must populate every single field designated for AI updates.**

    {{
      "marketNote": "Executor's Battle Card: {{ticker}}",
      "confidence": "Your **'Story' Label + Proof of Reasoning** (e.g., 'Trend_Bias: Bearish (Story_Confidence: Low) - Reasoning: The action was a *failure* against the Bearish trend...').",
      "screener_briefing": "Your **10-Part Regex-Friendly 'Data Packet'** (Setup_Bias, Justification, Catalyst, Pattern, Plan A, Plan B, S_Levels, R_Levels).",
      "basicContext": {{
        "tickerDate": "{{ticker}} | {{trade_date_str}}",
        "sector": "Set in Static Editor / Preserved",
        "companyDescription": "Set in Static Editor / Preserved",
        "priceTrend": "Your new summary of the cumulative trend.",
        "recentCatalyst": "Your 'Governing Narrative' (e.g., 'Post-earnings digestion continues; today's news confirmed...' or 'Awaiting Fed tariffs...')"
      }},
      "technicalStructure": {{
        "majorSupport": "Your *evolved* list of support zones, based on Historical Notes + new, multi-day Committed Buyer levels.",
        "majorResistance": "Your *evolved* list of resistance zones, based on Historical Notes + new, multi-day Committed Seller levels.",
        "pattern": "Your **'Structural Narrative'** (multi-day) describing the battle between these zones (e.g., 'Consolidating above $265...').",
        "volumeMomentum": "Your **Volume Analysis** from Task 6 (e.g., 'High-volume defense. The rejection of $239.15...')."
      }},
      "fundamentalContext": {{
        "valuation": "AI RULE: READ-ONLY (Set during initialization/manual edit)",
        "analystSentiment": "Carry over from [Previous Card] UNLESS new analyst ratings are found in [Overall Market Context].",
        "insiderActivity": "Carry over from [Previous Card] UNLESS new insider activity is found in [Overall Market Context].",
        "peerPerformance": "How did this stock perform *relative to its sector* or the `[Overall Market Context]`?"
      }},
      "behavioralSentiment": {{
        "buyerVsSeller": "Your **Conclusion** (e.g., 'Committed Buyers in control, having proven strength against a macro headwind...').",
        "emotionalTone": "Your **Pattern + Proof of Reasoning** (e.g., 'Accumulation (Stable) - Reasoning: (1. Observation) Price formed a higher low. (2. Inference) This is not a vacuum, it proves buyers are competing. (3. Conclusion) This signals seller exhaustion...').",
        "newsReaction": "Your **Headwind/Tailwind Analysis** (e.g., 'Showed extreme relative strength by holding support *despite* the bearish macro context...')."
      }},
      "openingTradePlan": {{
        "planName": "Your new primary plan for the *next* open (e.g., 'Long from $266.25 Support').",
        "knownParticipant": "Who is confirmed at the level, per your model? (e.g., 'Committed Buyers at $266').",
        "expectedParticipant": "Who acts if trigger hits? (e.g., 'Desperate Buyers (FOMO) on a break of $271').",
        "trigger": "Specific price action validating this plan.",
        "invalidation": "Price action proving this plan WRONG."
      }},
      "alternativePlan": {{
        "planName": "Your new competing plan (e.g., 'Failure at $271 Resistance').",
        "scenario": "When does this plan become active?",
        "knownParticipant": "Who is confirmed if scenario occurs?",
        "expectedParticipant": "Who acts if trigger hits?",
        "trigger": "Specific price action validating this plan.",
        "invalidation": "Price action proving this plan WRONG."
      }},
      "todaysAction": "STRICT: Max 4-5 sentences, under 2500 chars. Format: 'DATE: Pattern (State). [Brief 3-Act narrative of who acted at which key level and the outcome].'. Do NOT repeat content from other fields."
    }}
    """
    
    logger.log(f"3. Calling EOD AI Analyst for {ticker}...");
    
    # --- Strict Schema Safety Net ---
    company_card_schema = {
        "type": "OBJECT",
        "properties": {
            "marketNote": {"type": "STRING"},
            "confidence": {"type": "STRING"},
            "screener_briefing": {"type": "STRING"},
            "basicContext": {"type": "OBJECT", "properties": {"tickerDate": {"type": "STRING"}, "sector": {"type": "STRING"}, "companyDescription": {"type": "STRING"}, "priceTrend": {"type": "STRING"}, "recentCatalyst": {"type": "STRING"}}},
            "technicalStructure": {"type": "OBJECT", "properties": {"majorSupport": {"type": "STRING"}, "majorResistance": {"type": "STRING"}, "pattern": {"type": "STRING"}, "volumeMomentum": {"type": "STRING"}}},
            "fundamentalContext": {"type": "OBJECT", "properties": {"valuation": {"type": "STRING"}, "analystSentiment": {"type": "STRING"}, "insiderActivity": {"type": "STRING"}, "peerPerformance": {"type": "STRING"}}},
            "behavioralSentiment": {"type": "OBJECT", "properties": {"buyerVsSeller": {"type": "STRING"}, "emotionalTone": {"type": "STRING"}, "newsReaction": {"type": "STRING"}}},
            "openingTradePlan": {"type": "OBJECT", "properties": {"planName": {"type": "STRING"}, "knownParticipant": {"type": "STRING"}, "expectedParticipant": {"type": "STRING"}, "trigger": {"type": "STRING"}, "invalidation": {"type": "STRING"}}},
            "alternativePlan": {"type": "OBJECT", "properties": {"planName": {"type": "STRING"}, "scenario": {"type": "STRING"}, "knownParticipant": {"type": "STRING"}, "expectedParticipant": {"type": "STRING"}, "trigger": {"type": "STRING"}, "invalidation": {"type": "STRING"}}},
            "todaysAction": {"type": "STRING"}
        }
    }
    
    ai_response_text = call_gemini_api(prompt, system_prompt, logger, model_name=model_name, response_schema=company_card_schema, tracker_ticker=ticker)
    if not ai_response_text: 
        logger.log(f"Error: No AI response for {ticker}."); 
        return None
    
    logger.log(f"4. Received EOD Card for {ticker}. Parsing & Validating...")

    try:
        # Robust multi-format JSON parsing (handles direct JSON + markdown fences).
        # _safe_parse_ai_json returns None — never raises — on parse failure.
        ai_data = _safe_parse_ai_json(ai_response_text)
        if ai_data is None:
            raise json.JSONDecodeError(
                "_safe_parse_ai_json could not extract a valid JSON object", ai_response_text, 0
            )
        new_action = ai_data.pop("todaysAction", None)
        
        if not new_action:
            logger.log("Error: AI response is missing required fields ('todaysAction').")
            logger.log("--- DEBUG: RAW AI OUTPUT ---")
            # This will print the raw JSON to your Streamlit log so you can inspect it
            logger.log_code(json.dumps(ai_data, indent=2), language='json') 
            return None
        
        # --- FIX: Rebuild the full card in Python ---
        
        # 1. Get a deep copy of the *previous* card to avoid mutating it
        import copy
        final_card = copy.deepcopy(previous_overview_card_dict)
        
        # 2. **Deeply update** the card with the new AI data
        # This merges the new data (plans, sentiment) while preserving read-only fields
        def deep_update(d, u):
            for k, v in u.items():
                if isinstance(v, dict):
                    d[k] = deep_update(d.get(k, {}), v)
                else:
                    d[k] = v
            return d
        
        final_card = deep_update(final_card, ai_data)
        
        # 3. Manually update fields the AI shouldn't control
        final_card['basicContext']['tickerDate'] = f"{ticker} | {trade_date_str}"

        # 'valuation' is READ-ONLY per GEMINI.md: "Set during initialization/manual edit".
        # The AI is instructed to echo the placeholder text, so deep_update would
        # overwrite the real valuation data.  Explicitly restore the previous value.
        prev_valuation = previous_overview_card_dict.get('fundamentalContext', {}).get('valuation', '')
        if prev_valuation:
            final_card.setdefault('fundamentalContext', {})['valuation'] = prev_valuation
        
        # 4. Programmatically append to the log
        if "technicalStructure" not in final_card:
            final_card['technicalStructure'] = {}
        if "keyActionLog" not in final_card['technicalStructure'] or not isinstance(final_card['technicalStructure']['keyActionLog'], list):
            final_card['technicalStructure']['keyActionLog'] = []
            
        # --- Remove the old, deprecated 'keyAction' field if it exists ---
        if 'keyAction' in final_card['technicalStructure']:
            del final_card['technicalStructure']['keyAction']

        # Prevent duplicate entries if re-running
        if not any(entry.get('date') == trade_date_str for entry in final_card['technicalStructure']['keyActionLog']):
            final_card['technicalStructure']['keyActionLog'].append({
                "date": trade_date_str,
                "action": new_action
            })
        else:
            logger.log(
                f"   ⚠️ IMMUTABILITY: Log entry for {trade_date_str} already exists in "
                f"{ticker} card.  Preserving original entry — not overwriting."
            )
            # Do NOT overwrite.  The keyActionLog is an immutable daily record per GEMINI.md:
            # "Users cannot manually edit the todaysAction log.
            #  It is an immutable record of the AI's daily analysis."

        # 5. --- FIX: REMOVED the lines that reset the trade plans ---
        # final_card['openingTradePlan'] = ...
        # final_card['alternativePlan'] = ...

        logger.log(f"--- Success: AI update for {ticker} complete. ---")
        final_json = json.dumps(final_card, indent=4)
        TRACKER.register_artifact(f"{ticker}_CARD", final_json)

        # --- QUALITY GATE: Validate output quality ---
        try:
            qr = validate_company_card(final_card, ticker=ticker, previous_card=previous_overview_card_dict)
            if not qr.passed:
                logger.warning(f"⚠️ QUALITY FAIL ({ticker}): {qr.critical_count} critical, {qr.warning_count} warnings")
                for issue in qr.issues:
                    if issue.severity == 'critical':
                        logger.warning(f"   🔴 [{issue.rule}] {issue.field}: {issue.message}")
                TRACKER.log_error(ticker, f"Quality: {qr.critical_count} critical issues")
            elif qr.warning_count > 0:
                logger.log(f"   📊 Quality: PASS with {qr.warning_count} warnings for {ticker}")
            else:
                logger.log(f"   📊 Quality: PERFECT for {ticker}")
        except Exception as qe:
            logger.warning(f"   ⚠️ Quality validator error: {qe}")

        return final_json # Return the full, new card

    except json.JSONDecodeError as e:
        logger.log(f"Error: Failed to decode AI response JSON for {ticker}. Details: {e}")
        logger.log_code(ai_response_text, language='text')
        return None
    except Exception as e:
        logger.log(f"Unexpected error validating AI response for {ticker}: {e}")
        return None

# --- REFACTORED: update_economy_card (PROMPT FULLY REBUILT) ---
def update_economy_card(
    current_economy_card: str, 
    daily_market_news: str, 
    model_name: str,
    selected_date: date, 
    logger: AppLogger = None
):
    """
    Updates the global Economy Card in the database using AI.
    --- FULL REBUILD: This prompt now forces a two-part synthesis:
    1. The "Why" (Narrative) from the Market Wrap.
    2. The "How" (Evidence) from the level-based ETF Summaries.
    ---
    """
    if logger is None:
        logger = AppLogger() 
    
    logger.log("--- Starting Economy Card EOD Update ---")

    try:
        previous_economy_card_dict = json.loads(current_economy_card)
    except (json.JSONDecodeError, TypeError):
        logger.log("   ...Warn: Could not parse previous card, starting from default.")
        previous_economy_card_dict = json.loads(DEFAULT_ECONOMY_CARD_JSON)

    # --- NEW: Extract the keyActionLog from the previous card ---
    previous_action_log = previous_economy_card_dict.get("keyActionLog", [])
    if isinstance(previous_action_log, list):
        recent_log_entries = previous_action_log[-5:] # Get last 5
    else:
        recent_log_entries = []

    logger.log("2. Building Economy Card Update Prompt...")
    
    trade_date_str = selected_date.isoformat()

    # --- IMPACT ENGINE INTEGRATION (ECONOMY) ---
    etf_impact_data = {}
    
    # Expanded Asset List (20 Assets)
    target_etfs = [
        # Major Indices
        "SPY", "QQQ", "IWM", "DIA",
        # Sectors
        "XLK", "XLF", "XLE", "XLV", "XLI", "XLC", "XLP", "XLU", "SMH",
        # Commodities & Macro
        "TLT", "UUP", "BTCUSDT", "PAXGUSDT", "CL=F", "EURUSDT", "^VIX"
    ]
    
    conn = get_db_connection()
    if conn:
        try:
            for etf in target_etfs:
                try:
                    # --- CACHING IMPLEMENTED ---
                    context_card = get_or_compute_context(conn, etf, trade_date_str, logger)
                    etf_impact_data[etf] = context_card
                    # logger.log(f"   ...Loaded Impact Context for {etf}") # Too verbose?
                except Exception as inner_e:
                    logger.log(f"   ...Failed to load context for {etf}: {inner_e}")
                    etf_impact_data[etf] = {"error": str(inner_e)}
        except Exception as e:
             logger.log(f"⚠️ Economy Engine Failed: {e}")
        finally:
            conn.close()
    
    combined_etf_evidence = "[IMPACT ENGINE CONTEXT]\\n" + json.dumps(etf_impact_data, indent=2)

    # --- FIX: Main Prompt ---
    system_prompt = (
        "You are an expert Macro Strategist. Your objective is to synthesize raw market news "
        "(The 'Why') with quantitative ETF price action (The 'How') to update the global Economy Card. "
        "Complete the required JSON schema accurately and comprehensively. "
        "CRITICAL RULE for 'todaysAction': This field is a CONCISE daily log entry (max 4-5 sentences, under 2500 characters). "
        "Format: 'DATE: [Macro Theme]. [Brief narrative of what drove markets today and the outcome].'. "
        "Do NOT dump the entire card analysis into todaysAction. It must be a short summary suitable for a daily log, not a replacement for the full card."
    )

    prompt = f"""
    [Previous Day's Economy Card (Read-Only)]
    (This is the established macro context. You must read this first.)
    <previous_economy_card>
    {json.dumps(previous_economy_card_dict, indent=2)}
    </previous_economy_card>

    [Log of Recent Key Actions (Read-Only)]
    (This is the day-by-day story so far. Use this for context.)
    <recent_key_actions>
    {json.dumps(recent_log_entries, indent=2)}
    </recent_key_actions>

    [Raw Market News Input (The 'Why' / Narrative Source)]
    (This contains RAW news headlines and snippets. You must synthesize the narrative 'Story' yourself from this data.)
    <raw_market_news>
    {daily_market_news or "No raw market news was provided."}
    </raw_market_news>

    [Key ETF Summaries (The 'How' / IMPACT CONTEXT CARDS)]
    (This is the quantitative, level-based 'proof'. Use the 'Value Migration Log', 'volume_profile', 'key_volume_events', and 'key_levels' for SPY, QQQ, etc. to confirm the narrative.)
    <key_etf_summaries>
    {combined_etf_evidence}
    </key_etf_summaries>
    """

    logger.log("3. Calling Macro Strategist AI...")
    
    # --- Strict Schema Safety Net ---
    economy_card_schema = {
        "type": "OBJECT",
        "properties": {
            "marketNarrative": {"type": "STRING"},
            "marketBias": {"type": "STRING"},
            "keyEconomicEvents": {"type": "OBJECT", "properties": {"last_24h": {"type": "STRING"}, "next_24h": {"type": "STRING"}}},
            "sectorRotation": {"type": "OBJECT", "properties": {"leadingSectors": {"type": "ARRAY", "items": {"type": "STRING"}}, "laggingSectors": {"type": "ARRAY", "items": {"type": "STRING"}}, "rotationAnalysis": {"type": "STRING"}}},
            "indexAnalysis": {"type": "OBJECT", "properties": {"pattern": {"type": "STRING"}, "SPY": {"type": "STRING"}, "QQQ": {"type": "STRING"}}},
            "interMarketAnalysis": {"type": "OBJECT", "properties": {"bonds": {"type": "STRING"}, "commodities": {"type": "STRING"}, "currencies": {"type": "STRING"}, "crypto": {"type": "STRING"}}},
            "marketInternals": {"type": "OBJECT", "properties": {"volatility": {"type": "STRING"}}},
            "todaysAction": {"type": "STRING"}
        }
    }
    
    ai_response_text = call_gemini_api(prompt, system_prompt, logger, model_name=model_name, response_schema=economy_card_schema, tracker_ticker="ECONOMY")
    if not ai_response_text:
        logger.log("Error: No response from AI for economy card update.")
        return None

    try:
        # Robust multi-format JSON parsing (handles direct JSON + markdown fences).
        ai_data = _safe_parse_ai_json(ai_response_text)
        if ai_data is None:
            raise json.JSONDecodeError(
                "_safe_parse_ai_json could not extract a valid JSON object", ai_response_text, 0
            )
        
        # --- FIX: Extract the 'todaysAction' ---
        new_action = ai_data.pop("todaysAction", None)
        
        if not new_action:
            logger.log("Error: AI response is missing required fields.")
            return None

        # --- FIX: Rebuild the full card in Python ---
        import copy
        final_card = copy.deepcopy(previous_economy_card_dict)
        
        # 2. **Deeply update** the card with the new AI data
        def deep_update(d, u):
            for k, v in u.items():
                if isinstance(v, dict):
                    d[k] = deep_update(d.get(k, {}), v)
                else:
                    d[k] = v
            return d
            
        final_card = deep_update(final_card, ai_data)
        
        # 3. Programmatically append to the log
        if "keyActionLog" not in final_card or not isinstance(final_card['keyActionLog'], list):
            final_card['keyActionLog'] = []
        
        # --- Remove the old, deprecated 'marketKeyAction' field if it exists ---
        if 'marketKeyAction' in final_card:
            del final_card['marketKeyAction']

        if not any(entry.get('date') == trade_date_str for entry in final_card['keyActionLog']):
            final_card['keyActionLog'].append({
                "date": trade_date_str,
                "action": new_action
            })
        else:
            logger.log(
                f"   ⚠️ IMMUTABILITY: Log entry for {trade_date_str} already exists in "
                f"economy card.  Preserving original entry — not overwriting."
            )
            # Do NOT overwrite.  The keyActionLog is an immutable daily record.

        logger.log("--- Success: Economy Card generation complete! ---")
        final_json = json.dumps(final_card, indent=4)
        TRACKER.register_artifact("ECONOMY_CARD", final_json)

        # --- QUALITY GATE: Validate output quality ---
        try:
            qr = validate_economy_card(final_card)
            if not qr.passed:
                logger.warning(f"⚠️ QUALITY FAIL (ECONOMY): {qr.critical_count} critical, {qr.warning_count} warnings")
                for issue in qr.issues:
                    if issue.severity == 'critical':
                        logger.warning(f"   🔴 [{issue.rule}] {issue.field}: {issue.message}")
                TRACKER.log_error("ECONOMY", f"Quality: {qr.critical_count} critical issues")
            elif qr.warning_count > 0:
                logger.log(f"   📊 Quality: PASS with {qr.warning_count} warnings for ECONOMY")
            else:
                logger.log(f"   📊 Quality: PERFECT for ECONOMY")
        except Exception as qe:
            logger.warning(f"   ⚠️ Quality validator error: {qe}")

        return final_json
        
    except json.JSONDecodeError as e:
        logger.log(f"Error: Failed to decode AI response for economy card. Details: {e}")
        logger.log_code(ai_response_text, language='text')
        return None
    except Exception as e:
        logger.log(f"An unexpected error occurred during economy card update: {e}")
        return None