from __future__ import annotations

import requests
import json
import re
import time
import logging
from datetime import date
from deepdiff import DeepDiff
import streamlit as st 

# --- Core Module Imports ---
# 1. FIX: Removed API_KEYS. 
# 2. FIX: Imported KEY_MANAGER (The initialized instance)
from modules.config import (
    API_BASE_URL, 
    MODEL_NAME,
    KEY_MANAGER, 
    DEFAULT_COMPANY_OVERVIEW_JSON, 
    DEFAULT_ECONOMY_CARD_JSON
)
# 3. FIX: Added missing newline that was causing syntax errors
from modules.data_processing import parse_raw_summary
from modules.ui_components import AppLogger

# --- REMOVED: The entire "NEW, ROBUST GLOBAL KEY MANAGER SETUP" block ---
# We do not initialize KeyManager here anymore. It is imported from modules.config.

# --- The Robust API Caller ---
def call_gemini_api(prompt: str, system_prompt: str, logger: AppLogger, model_name: str, max_retries=5) -> str | None:
    """
    Calls Gemini API using dynamic model selection and quota management.
    """
    if not KEY_MANAGER:
        logger.log("❌ ERROR: KeyManager not initialized.")
        return None
    
    for i in range(max_retries):
        current_api_key = None
        key_name = "Unknown"

        try:
            # 1. ACQUIRE: Request key specifically for this model's bucket
            key_name, current_api_key, wait_time = KEY_MANAGER.get_key(target_model=model_name)
            
            if not current_api_key:
                logger.log(f"⏳ All keys exhausted for {model_name}. Waiting {wait_time:.0f}s... (Attempt {i+1})")
                if wait_time > 0 and i < max_retries - 1:
                    time.sleep(wait_time)
                    continue
                else:
                    logger.log(f"❌ ERROR: Global rate limit reached for {model_name}.")
                    return None
            
            logger.log(f"🔑 Acquired '{key_name}' | Model: {model_name} (Attempt {i+1})")
            
            # 2. USE: Construct Dynamic URL
            # --- CHANGE IS HERE ---
            gemini_url = f"{API_BASE_URL}/{model_name}:generateContent?key={current_api_key}"
            
            payload = {
                "contents": [{"parts": [{"text": prompt}]}], 
                "systemInstruction": {"parts": [{"text": system_prompt}]}
            }
            headers = {'Content-Type': 'application/json'}
            
            response = requests.post(gemini_url, headers=headers, data=json.dumps(payload), timeout=60)
            
            # 3. REPORT: Pass model_name for correct counter increment
            if response.status_code == 200:
                KEY_MANAGER.report_success(current_api_key, model_id=model_name)
                
                result = response.json()
                try:
                    return result["candidates"][0]["content"]["parts"][0]["text"].strip()
                except (KeyError, IndexError):
                    logger.log(f"⚠️ Invalid JSON: {result}")
                    KEY_MANAGER.report_failure(current_api_key, is_server_error=True)
                    continue 

            elif response.status_code == 429:
                logger.log(f"⛔ 429 Rate Limit on '{key_name}'. Adding Strike.")
                KEY_MANAGER.report_failure(current_api_key, is_server_error=False)
            elif response.status_code >= 500:
                logger.log(f"☁️ {response.status_code} Server Error. Waiting 10s...")
                KEY_MANAGER.report_failure(current_api_key, is_server_error=True)
                time.sleep(10) # Give the server breathing room
            else:
                logger.log(f"⚠️ API Error {response.status_code}: {response.text}")
                KEY_MANAGER.report_failure(current_api_key, is_server_error=True)

        except Exception as e:
            logger.log(f"💥 Exception: {str(e)}")
            if current_api_key:
                KEY_MANAGER.report_failure(current_api_key, is_server_error=True)
        
        if i < max_retries - 1:
            time.sleep(2 ** i)

    logger.log("❌ FATAL: Max retries exhausted.")
    return None
    

# --- REFACTORED: update_company_card (PROMPT IS GOOD) ---
def update_company_card(
    ticker: str, 
    previous_card_json: str, 
    previous_card_date: str, 
    historical_notes: str, 
    new_eod_summary: str, 
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
        logger = AppLogger() # Removed st_container=None

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
        "You are an expert market structure analyst. Your *only* job is to apply the specific 4-Participant Trading Model provided in the user's prompt. "
        "Your logic must *strictly* follow this model. You will be given a 'Masterclass' in the prompt that defines the model's philosophy. "
        "Your job has **four** distinct analytical tasks: "
        "1. **Analyze `behavioralSentiment` (The 'Micro'):** You MUST provide a full 'Proof of Reasoning' for the `emotionalTone` field. "
        "2. **Analyze `technicalStructure` (The 'Macro'):** Use *repeated* participant behavior to define and evolve the *key structural zones*. "
        "3. **Calculate `confidence` (The 'Story'):** You MUST combine the lagging 'Trend_Bias' with the 'Story_Confidence' (H/M/L) and provide a full justification. "
        "4. **Calculate `screener_briefing` (The 'Tactic'):** You MUST synthesize your *entire* analysis to calculate a *new, separate, actionable* 'Setup_Bias' and assemble the final Python-readable data packet. "
        "Do not use any of your own default logic. Your sole purpose is to be a processor for the user's provided framework."
    )

    
    trade_date_str = new_eod_date.isoformat()

    # --- FINAL Main 'Masterclass' Prompt ---
    prompt = f"""
    [Overall Market Context for Today]
    (This is the macro "Headwind" or "Tailwind" for the day. Use this for the 'newsReaction' field. It also contains company-specific news.)
    {market_context_summary or "No overall market context was provided."}

    [Historical Notes for {ticker}]
    (CRITICAL STATIC CONTEXT: These are the MAJOR structural levels. LEVELS ARE PARAMOUNT.)
    {historical_notes or "No historical notes provided."}
    
    [Previous Card (Read-Only)]
    (This is the established structure, plans, and `keyActionLog` so far. Read this for the 3-5 day context AND to find the previous 'recentCatalyst' and 'fundamentalContext' data.) 
    {json.dumps(previous_overview_card_dict, indent=2)}

    [Log of Recent Key Actions (Read-Only)]
    (This is the day-by-day story so far. Use this for context.)
    {json.dumps(recent_log_entries, indent=2)}

    [Today's New Price Action Summary (for {trade_date_str})]
    (This is the objective, level-based data for the day you must analyze.)
    {new_eod_summary}

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
        * Describe ONLY how volume from `[Today's New Price Action Summary]` *confirmed or denied* the action *at the specific levels*.
        * **Example 1 (Confirmation):** "High-volume defense. The rejection of the $239.15 low was confirmed by the day's highest volume spike, proving Committed Buyers were present in force."
        * **Example 2 (No Confirmation):** "Low-volume breakout. The move above $420 resistance was on low, unconvincing volume, signaling a 'Stable Market' (Committed Seller) exhaustion, not 'Unstable' (Desperate Buyer) panic."

    **7. `behavioralSentiment` Section (The "Micro" / Today's Analysis):**
        * **`emotionalTone` (Pattern + Proof of Reasoning):**
            * This is your **Justification**, not a description. You MUST show your work by following this 3-part logic:
            * **1. Observation (The "What"):** State the objective price action from `[Today's Summary]` (e.g., "Price formed a higher-low pattern...").
            * **2. Inference (The "So What?"):** State what this action *means* according to the Masterclass philosophy. (e.g., "This action is the *opposite* of a 'price vacuum' (which would signal `Desperate Sellers`). It proves buyers who were 'absent' are now becoming more aggressive.").
            * **3. Conclusion (The "Why"):** State the *psychological event* this signals. (e.g., "Therefore, this price action is the classic signal of **seller exhaustion** and a shift to *competing* **Committed Buyers**.").
            * **Final Format:** "Label - Reasoning: [Your full 3-part proof]"
            * **Example:** "Accumulation (Stable) - Reasoning: **(1. Observation)** The `[Price Summary]` shows sellers were unable to achieve a new low with conviction, instead forming a higher-low pattern. **(2. Inference)** This action is *not* a 'price vacuum' (which would signal `Desperate Sellers`); it proves that buyers who were 'absent' are now competing more aggressively. **(3. Conclusion)** This directly signals **seller exhaustion** and a shift to competing **Committed Buyers** as defined in the Masterclass."
        * **`newsReaction` (Headwind/Tailwind):**
            * This is your *relative strength* analysis. Compare the stock's `emotionalTone` to the `[Overall Market Context]`.
            * (e.g., "Market was bearish (headwind), but the stock held its $266 support in an 'Accumulation' pattern. This shows *extreme relative strength*...")
        * **`buyerVsSeller` (The Conclusion):**
            * This is your *final synthesis* of the `emotionalTone` and `newsReaction`.
            * (e.g., "Committed Buyers are in firm control. They not only showed a 'Stable Accumulation' pattern at $415 but did so *against* a weak, bearish market, confirming their high conviction.")

    **8. `keyActionLog`:** Write your `todaysAction` log entry *last*, using the language from your `behavioralSentiment` analysis.
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
      "marketNote": "Executor's Battle Card: {ticker}",
      "confidence": "Your **'Story' Label + Proof of Reasoning** (e.g., 'Trend_Bias: Bearish (Story_Confidence: Low) - Reasoning: The action was a *failure* against the Bearish trend...').",
      "screener_briefing": "Your **10-Part Regex-Friendly 'Data Packet'** (Setup_Bias, Justification, Catalyst, Pattern, Plan A, Plan B, S_Levels, R_Levels).",
      "basicContext": {{
        "tickerDate": "{ticker} | {trade_date_str}",
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
      "todaysAction": "A single, detailed log entry for *only* today's action, *using the language from your Masterclass analysis*."
    }}
    """
    
    logger.log(f"3. Calling EOD AI Analyst for {ticker}...");
    
    ai_response_text = call_gemini_api(prompt, system_prompt, logger, model_name=model_name)
    if not ai_response_text: 
        logger.log(f"Error: No AI response for {ticker}."); 
        return None
    
    logger.log(f"4. Received EOD Card for {ticker}. Parsing & Validating...")
    json_match = re.search(r"```json\s*([\s\S]+?)\s*```", ai_response_text)
    ai_response_text = json_match.group(1) if json_match else ai_response_text.strip()
    
    try:
        ai_data = json.loads(ai_response_text)
        new_action = ai_data.pop("todaysAction", None)
        
        if not new_action:
            logger.log("Error: AI response is missing required fields ('todaysAction').")
            logger.log("--- DEBUG: RAW AI OUTPUT ---")
            # This will print the raw JSON to your Streamlit log so you can inspect it
            logger.log_code(json.dumps(ai_data, indent=2), language='json') 
            return None
        
        # --- FIX: Rebuild the full card in Python ---
        
        # 1. Get a fresh copy of the *previous* card
        final_card = previous_overview_card_dict.copy()
        
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
            logger.log("   ...Log entry for this date already exists. Overwriting.")
            # Find and overwrite the existing entry
            for i, entry in enumerate(final_card['technicalStructure']['keyActionLog']):
                if entry.get('date') == trade_date_str:
                    final_card['technicalStructure']['keyActionLog'][i] = {
                        "date": trade_date_str,
                        "action": new_action
                    }
                    break

        # 5. --- FIX: REMOVED the lines that reset the trade plans ---
        # final_card['openingTradePlan'] = ...
        # final_card['alternativePlan'] = ...

        logger.log(f"--- Success: AI update for {ticker} complete. ---")
        return json.dumps(final_card, indent=4) # Return the full, new card

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
    etf_summaries: str, 
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
        logger = AppLogger() # Removed st_container=None
    
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
    
    # --- FIX: Rebuilt System Prompt ---
    system_prompt = (
        "You are a macro-economic strategist. Your task is to update the *entire* global 'Economy Card' JSON. "
        "Your primary goal is a **two-part synthesis**: "
        "1. Identify the **narrative ('Why')** from the `[Market Wrap News]`. "
        "2. Find the **level-based evidence ('How')** in the `[Key ETF Summaries]` (VWAP, POC, ORL/ORH) to **prove or disprove** that narrative. "
        "You must continue the story from the previous card, evaluating how today's data confirms, contradicts, or changes the established trend."
    )
    
    trade_date_str = selected_date.isoformat()

    # --- FIX: Rebuilt Main Prompt with two-part synthesis logic ---
    prompt = f"""
    [Previous Day's Economy Card (Read-Only)]
    (This is the established macro context. You must read this first.)
    {json.dumps(previous_economy_card_dict, indent=2)}

    [Log of Recent Key Actions (Read-Only)]
    (This is the day-by-day story so far. Use this for context.)
    {json.dumps(recent_log_entries, indent=2)}

    [Market Wrap News (The 'Why' / Narrative)]
    (This is the qualitative 'story' for the day.)
    {daily_market_news or "No market wrap news was provided."}

    [Key ETF Summaries (The 'How' / Level-Based Evidence)]
    (This is the quantitative, level-based 'proof'. Use VWAP, POC, ORL/ORH, VAH/VAL to confirm the narrative.)
    {etf_summaries or "No ETF summaries were provided."}

    [Your Task for {trade_date_str}]
    Based on *all* the information above, generate a new, complete JSON object by following
    these rules to fill the template below.

    **Master Rule (Weighted Synthesis - 60/40 Logic):**
    Your primary goal is to determine the **governing short-term trend** (the "story" from the last 3-5 days) and then evaluate if **today's action** (the new data) *confirms, contradicts, or changes* that trend.

    1.  **Identify the "Governing Trend" (The 60% Weight):**
        * First, read the `marketBias` and `indexAnalysis` from the `[Previous Day's Card]` and the `[Log of Recent Key Actions]`.
        * This gives you the established narrative. (e.g., "SPY is in a 3-day bearish channel, failing at $450.")

    2.  **Evaluate "Today's Data" (The 40% Weight):**
        * **Synthesize BOTH data sources.** Read the `[Market Wrap News]` for the narrative (e.g., "breadth was weak").
        * **Then, verify** that narrative using the `[Key ETF Summaries]` (e.g., "This is confirmed: IWM and DIA broke their ORLs and closed below VWAP, while QQQ held its VAL.").
        * The *quality* of the move (proven by levels) is more important than the direction.

    3.  **Synthesize (The New `marketBias` and `marketNarrative`):**
        * Your `marketNarrative` must explain this two-part synthesis.
        * **If Today's Data CONFIRMS the trend:** The `marketBias` is strengthened. (e.g., "A low-volume rally into resistance, confirmed by IWM closing below its POC, *confirms* the bearish trend. Bias remains `Bearish`.")
        * **If Today's Data is just NOISE:** The `marketBias` is unchanged.
        * **If Today's Data CHANGES the trend:** The `marketBias` can flip. This *must* be a high-conviction event, supported by *both* the narrative and strong level-based breaks in the ETFs (e.g., "SPY broke *above* the $450 channel on high volume, with QQQ and IWM also closing above their VAH. The governing trend is now changing. Bias moves to `Neutral` or `Bullish`.")

    **Detailed "Story-Building" Rules:**

    * **`keyEconomicEvents`:** Populate this *directly* from the "REAR VIEW" and "COMING UP" sections of the `[Market Wrap News]`.
    * **`indexAnalysis` (Story-Building):**
        * Read the `indexAnalysis` from the `[Previous Day's Card]`.
        * Using today's `[Market Wrap News]` *and* the specific levels from the `[Key ETF Summaries]`, write the **new, updated** analysis.
        * **You MUST cite level-based evidence.** (e.g., "SPY *failed at* $450 resistance, as noted in the Market Wrap, and this was **confirmed by the ETF data** showing a close below VWAP ($448.50) and the POC ($449.00).").
    * **`sectorRotation` (Story-Building):**
        * Read the `sectorRotation` analysis from the `[Previous Day's Card]`.
        * Using today's ETF data (XLK, XLF, etc.), update the `leadingSectors`, `laggingSectors`, and `rotationAnalysis`.
        * **Cite level-based evidence.** (e.g., "Tech (XLK) *was* a leading sector for 5 days but saw profit-taking today, **closing below its VAH ($303.78)**, moving it to lagging...").
    * **`interMarketAnalysis` (Story-Building):**
        * Read the `interMarketAnalysis` from the `[Previous Day's Card]`.
        * Using the `[Market Wrap News]` ("FIXED INCOME", "CRUDE", "FX" sections) *and* the `[Key ETF Summaries]` for TLT, GLD, UUP, **continue the narrative**.
        * (e.g., "Bonds (TLT) *continued* their decline, *confirming* the risk-on flow by **breaking below VAL ($89.60)**..." or "The Dollar (UUP) was choppy, **crossing VWAP ($28.23) multiple times** as the Market Wrap noted...").
    * **`todaysAction` (The Log):** Create a *new, single log entry* for today's macro action, referencing both the Market Wrap narrative and key ETF level interactions.

    **MISSING DATA RULE (CRITICAL):**
    * If `[Market Wrap News]` or `[Key ETF Summaries]` are missing, empty, or clearly irrelevant, you **MUST** state this in the relevant analytical fields.
    * **DO NOT** silently copy yesterday's data.
    * *(Example: `indexAnalysis.SPY`: "No new ETF data was provided to update the analysis.")*

    [Output Format Constraint]
    Output ONLY a single, valid JSON object in this exact format. **You must populate every single field.**

    {{
      "marketNarrative": "Your new high-level narrative (based on the Master Rule).",
      "marketBias": "Your new bias (e.g., 'Bullish', 'Bearish', 'Neutral') (based on the Master Rule).",
      "keyEconomicEvents": {{
        "last_24h": "Your summary from Market Wrap 'REAR VIEW'.",
        "next_24h": "Your summary from Market Wrap 'COMING UP'."
      }},
      "sectorRotation": {{
        "leadingSectors": ["List", "of", "leading", "sectors"],
        "laggingSectors": ["List", "of", "lagging", "sectors"],
        "rotationAnalysis": "Your 'Story-Building' analysis of the sector rotation, citing level-based evidence."
      }},
      "indexAnalysis": {{
        "pattern": "Your new high-level summary of the *main indices* pattern.",
        "SPY": "Your 'Story-Building' analysis of SPY, citing level-based evidence (VWAP, POC, VAH/VAL).",
        "QQQ": "Your 'Story-Building' analysis of QQQ, citing level-based evidence (VWAP, POC, VAH/VAL)."
      }},
      "interMarketAnalysis": {{
        "bonds": "Your 'Story-Building' analysis of TLT/bonds (citing Market Wrap and level-based data).",
        "commodities": "Your 'Story-Building' analysis of GLD/Oil (citing Market Wrap and level-based data).",
        "currencies": "Your 'Story-Building' analysis of UUP/Dollar (citing Market Wrap and level-based data).",
        "crypto": "Your 'Story-Building' analysis of Crypto/BTC (citing level-based data)."
      }},
      "marketInternals": {{
        "volatility": "Your analysis of VIX/volatility."
      }},
      "todaysAction": "A single, detailed log entry for *only* today's macro action, referencing key ETFs and news."
    }}
    """

    logger.log("3. Calling Macro Strategist AI...")
    
    ai_response_text = call_gemini_api(prompt, system_prompt, logger, model_name=model_name)
    if not ai_response_text:
        logger.log("Error: No response from AI for economy card update.")
        return None

    logger.log("4. Received new Economy Card. Parsing and validating...")
    json_match = re.search(r"```json\s*([\s\S]+?)\s*```", ai_response_text)
    if json_match:
        ai_response_text = json_match.group(1)
    
    try:
        # --- FIX: We are now parsing the AI's *new* output ---
        ai_data = json.loads(ai_response_text)
        
        # --- FIX: Extract the 'todaysAction' ---
        new_action = ai_data.pop("todaysAction", None)
        
        if not new_action:
            logger.log("Error: AI response is missing required fields.")
            return None

        # --- FIX: Rebuild the full card in Python ---
        final_card = previous_economy_card_dict.copy()
        
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
            logger.log("   ...Log entry for this date already exists. Overwriting.")
            for i, entry in enumerate(final_card['keyActionLog']):
                if entry.get('date') == trade_date_str:
                    final_card['keyActionLog'][i] = {
                        "date": trade_date_str,
                        "action": new_action
                    }
                    break

        logger.log("--- Success: Economy Card generation complete! ---")
        return json.dumps(final_card, indent=4)
        
    except json.JSONDecodeError as e:
        logger.log(f"Error: Failed to decode AI response for economy card. Details: {e}")
        logger.log_code(ai_response_text, language='text')
        return None
    except Exception as e:
        logger.log(f"An unexpected error occurred during economy card update: {e}")
        return None