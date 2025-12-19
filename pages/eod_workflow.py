import streamlit as st
# import sqlite3 <-- REMOVED
import os
import re
import json
from datetime import date, datetime, timedelta
from libsql_client import LibsqlError # <-- NEW: Import the correct error class

try:
    from pytz import timezone as pytz_timezone
    US_EASTERN = pytz_timezone('US/Eastern')
except ImportError:
    st.warning("`pytz` library not found. Using basic timezone info.")
    # This is a simplified fallback and might not handle DST correctly.
    class EST(datetime.tzinfo):
        def utcoffset(self, dt):
            return timedelta(hours=-5)
        def dst(self, dt):
            return timedelta(0)
        def tzname(self, dt):
            return "EST"
    US_EASTERN = EST()

# --- Local Imports ---
from modules.config import (
    AVAILABLE_MODELS,
    STOCK_TICKERS,
    ETF_TICKERS,
    DEFAULT_COMPANY_OVERVIEW_JSON,
    DEFAULT_ECONOMY_CARD_JSON,
    MODEL_NAME
)

# --- Corrected Imports ---
from modules.ai_services import call_gemini_api, KEY_MANAGER
from modules.data_processing import generate_analysis_text, parse_raw_summary, split_stock_summaries
from modules.db_utils import (
    get_all_tickers_from_db, 
    upsert_daily_inputs, 
    get_daily_inputs, 
    get_economy_card, # This now gets the most recent from archive
    get_company_card_and_notes, # This now gets the most recent from archive
    get_all_archive_dates,
    get_all_tickers_for_archive_date,
    get_archived_economy_card,
    get_archived_company_card,
    get_db_connection, # <-- This now returns a Turso client
    get_latest_daily_input_date 
)
from modules.ui_components import (
    AppLogger,
    display_view_market_note_card,
    display_editable_market_note_card,
    display_view_economy_card,
    display_editable_economy_card
)
from modules.ai_services import update_company_card, update_economy_card


################################################################################
# --- STREAMLIT APPLICATION UI ---
################################################################################

st.set_page_config(page_title="Analyst Pipeline (EOD)", layout="wide")
st.title("Analyst Pipeline Engine (EOD & Editor)")

# --- NEW: Validation Check for Key Manager ---
if not KEY_MANAGER:
    st.error("❌ CRITICAL: Gemini Key Manager failed to initialize.")
    st.info("Please check your [turso] credentials in `.streamlit/secrets.toml`.")
    st.stop()

# --- Get and display the "Global Living Date" ---
latest_update_date_str = get_latest_daily_input_date()
latest_update_date = None
if latest_update_date_str:
    st.subheader(f"Last Processed Date: {latest_update_date_str}")
    latest_update_date = datetime.strptime(latest_update_date_str, "%Y-%m-%d").date()
    default_pipeline_date = (latest_update_date + timedelta(days=1))
else:
    st.warning("No updates found. Ready to process the first day's data.")
    default_pipeline_date = datetime.now(US_EASTERN).date() - timedelta(days=1)


# --- Initialize session state ---
# (This section is unchanged)
if 'edit_mode' not in st.session_state: st.session_state['edit_mode'] = False
if 'edit_mode_economy' not in st.session_state: st.session_state['edit_mode_economy'] = False
if 'ticker_index' not in st.session_state: st.session_state['ticker_index'] = 0
if 'ticker_selector' not in st.session_state: st.session_state['ticker_selector'] = None
if 'current_selected_date' not in st.session_state: st.session_state['current_selected_date'] = None

# --- FIX 1: Initialize the processing date in session state ---
if 'processing_date' not in st.session_state:
    st.session_state.processing_date = default_pipeline_date


# --- Define Tabs ---
tab_runner_eod, tab_editor = st.tabs([
    "Pipeline Runner (EOD)",
    "Card Editor", # Renamed
])

# --- TAB 1: Pipeline Runner (EOD) ---
with tab_runner_eod:
    st.header("Date-Aware EOD Workflow")

    # --- NEW: Model Selector ---
    col_status, col_model = st.columns([2, 1])
    with col_status:
        st.info("✅ Gemini Rotation System: Active & Connected to Database")
    with col_model:
        try:
            default_index = AVAILABLE_MODELS.index(MODEL_NAME)
        except ValueError:
            default_index = 0

        selected_model = st.selectbox(
            "Select AI Model", 
            AVAILABLE_MODELS, 
            index=default_index, 
            help="Higher intelligence (Pro) uses more quota. Flash is faster."
        )

    # --- FIX 2: Bind the date_input to session state using `key` ---
    selected_date = st.date_input(
        "Select the Date to Process",
        key="processing_date",  # <-- This binds it to st.session_state.processing_date
        # value=default_pipeline_date, <-- This is no longer needed
        help="Defaults to the day after the last successfully processed date."
    )
    
    # --- "GAP DETECTION" GUARDRAIL ---
    # (This section is unchanged)
    if latest_update_date and selected_date > latest_update_date:
        day_diff = (selected_date - latest_update_date).days
        if day_diff > 1:
            is_just_weekend = (latest_update_date.weekday() == 4 and selected_date.weekday() == 0 and day_diff == 3)
            if not is_just_weekend:
                st.warning(
                    f"**Gap Detected:** You are about to process {selected_date.isoformat()}, "
                    f"but the last processed date was {latest_update_date.isoformat()}. "
                    f"You are skipping {day_diff - 1} day(s). Please ensure this is intentional (e.g., due to a holiday)."
                )
    st.divider()

    # --- STEP 1: (REFACTORED) ---
    st.subheader("Step 1: Save Manual Daily Inputs")
    st.caption("Provide the manual, qualitative market summary for the selected date.")

    # --- Get the saved news to show in the text box ---
    saved_market_news, saved_stock_summaries = get_daily_inputs(selected_date)

    with st.form("daily_inputs_form"):
        market_news_input = st.text_area(
            "Raw Market/Company News Input:",
            value=saved_market_news or "", # Load saved news
            height=300, # Increased height for raw dumps
            key="daily_market_news",
            help="Paste RAW news headlines, snippets, or bullet points from various sources here. The AI will synthesize the story."
        )
        
        if st.form_submit_button("💾 Save Manual Inputs", use_container_width=True):
            if not market_news_input:
                st.warning("Please provide a Market News Summary before saving.")
            else:
                # We save the news and also the *existing* stock summaries
                # This prevents the save button from deleting processed stock data
                # --- FIX: Pass 'stock_summaries' to the correct param ---
                if upsert_daily_inputs(selected_date, market_news_input):
                    st.success(f"Manual inputs for {selected_date.isoformat()} saved successfully.")
                    st.rerun()
                else:
                    st.error("Failed to save daily inputs. Check logs for details.")
    st.divider()

    # --- STEP 2: (REFACTORED) ---
    st.subheader("Step 2: Generate & Update Economy Card")
    
    # --- "GUARDRAIL" ---
    market_news_step2, _ = get_daily_inputs(selected_date)
    if not market_news_step2:
        st.warning(f"Please complete Step 1 (Save Manual Inputs) for {selected_date.isoformat()} before running this step.")
        st.stop()
    
    st.caption("This will run the ETF data processor and then immediately run the AI analysis to generate the Economy Card.")
    
    # --- "PRE-FLIGHT CHECK" INFO BOX ---
    st.info(
        """
        **Inputs for this step:**
        1.  **ETF Data:** Runs the `generate_analysis_text` function for all ETFs.
        2.  **Manual News:** Reads the news you saved in Step 1.
        3.  **Previous Card:** Reads the most recent card from the `economy_cards` table.
        """
    )
    
    log_container_eco = st.empty()

    if st.button("Generate & Update Economy Card", use_container_width=True):
        log_expander_eco = log_container_eco.expander("Economy Card Update Log", expanded=True)
        logger = AppLogger(log_expander_eco)
        
        success = False 
        
        with st.spinner(f"Updating Economy Card for {selected_date.isoformat()}..."):
            # --- MERGED STEP 2a: Run ETF Processor ---
            logger.log(f"1. Generating ETF Summaries for {selected_date.isoformat()}...")
            logger.log(f"   Using ETFs: {ETF_TICKERS}")
            
            etf_summaries = generate_analysis_text(ETF_TICKERS, selected_date)
            
            # --- FAIL-FAST CHECK (Missing Data) ---
            if "[ERROR]" in etf_summaries or "No data found" in etf_summaries:
                # Identify which ones failed for better user feedback
                failed_etfs = []
                # Simple check: extract ticker from lines containing [ERROR]
                # Format: "Data Extraction Summary: TICKER | DATE... [ERROR]..."
                # Since we have the map, let's use it.
                etf_summary_map_check = split_stock_summaries(etf_summaries)
                
                # If map is empty but error exists, it might be a global fetch error
                if not etf_summary_map_check:
                     logger.log(f"❌ **STOPPING Economy Card:** Global data fetch failure (No data for ANY requested ETF).")
                     st.stop()

                for ticker, text in etf_summary_map_check.items():
                    if "[ERROR]" in text or "No data found" in text:
                        failed_etfs.append(ticker)
                        logger.log(f"   ⚠️ Failure Details for {ticker}:")
                        logger.log_code(text, "text")
                
                if failed_etfs:
                    logger.log(f"❌ **STOPPING Economy Card:** Missing data for: {', '.join(failed_etfs)}")
                    logger.log(f"   Please check the 'market_data' table in Turso for these specific tickers on {selected_date.isoformat()}.")
                else:
                    logger.log(f"❌ **STOPPING Economy Card:** Unknown extraction error.")

                st.stop()

            # --- DETAILED VERIFICATION LOGGING ---
            # Parse the massive string to find verification blocks for each ETF
            etf_summary_map = split_stock_summaries(etf_summaries)
            for ticker, summary_text in etf_summary_map.items():
                v_match = re.search(r"\[VERIFICATION\](.*?)(\n\n|$)", summary_text, re.DOTALL)
                if v_match:
                    v_info = v_match.group(1).strip()
                    # Log a condensed version
                    lines = v_info.split('\n')
                    rows_line = next((l for l in lines if "Rows Fetched" in l), "Rows: ?")
                    logger.log(f"   📊 **{ticker}**: Source: Turso DB | {rows_line}")

            if "Data Extraction Summary:" not in etf_summaries:
                logger.log(f"❌ **Error:** Failed to generate ETF summaries. Processor returned: {etf_summaries}")
                st.stop()
            
            logger.log("   ...All ETF summaries generated & verified successfully.")
            # --- END MERGED STEP ---

            market_news, _ = get_daily_inputs(selected_date) # Get the manual news
            # --- FIX: Fetch context strictly BEFORE valid date ---
            current_economy_card_json, fetched_card_date = get_economy_card(before_date=selected_date.isoformat())
            
            # --- VERIFICATION LOGGING ---
            if fetched_card_date:
                logger.log(f"2. Found Previous Economy Card dated: **{fetched_card_date}**")
                if fetched_card_date == selected_date.isoformat():
                    logger.log("   ⚠️ **NOTE:** You are re-running for the SAME date. The AI will see the previous run's output as context.")
                else:
                    logger.log(f"   (Context is from {fetched_card_date}, processing for {selected_date.isoformat()})")
            else:
                logger.log("2. No previous Economy Card found. Starting fresh (Default Template).")
            
            try:
                logger.log("3. Calling AI to generate updated Economy Card...")
                
                # --- MERGED STEP 2b: Run AI Analysis ---
                updated_card_str = update_economy_card( 
                    current_economy_card=current_economy_card_json,
                    daily_market_news=market_news,
                    etf_summaries=etf_summaries, 
                    selected_date=selected_date,
                    logger=logger,
                    model_name=selected_model # <--- ADD THIS ARGUMENT
                )

                if not updated_card_str:
                    logger.log("❌ **Error:** Failed to generate new economy card. AI service returned no data.")
                else:
                    new_card_data = json.loads(updated_card_str)
                    new_card_json = json.dumps(new_card_data, indent=4)
                    logger.log("4. Successfully generated and validated the new Economy Card.")
                    
                    # --- NEW: Use Turso client directly ---
                    conn_eco_save = None
                    try:
                        conn_eco_save = get_db_connection()
                        # --- Save to the *correct* tables ---
                        # We save the ETF summary *with* the card
                        conn_eco_save.execute(
                            """
                            INSERT INTO economy_cards (date, raw_text_summary, economy_card_json)
                            VALUES (?, ?, ?)
                            ON CONFLICT(date) DO UPDATE SET
                                raw_text_summary = excluded.raw_text_summary,
                                economy_card_json = excluded.economy_card_json
                            """,
                            (selected_date.isoformat(), etf_summaries, new_card_json)
                        )
                        # No .commit() needed
                        logger.log(f"✅ **Success:** Saved ETF summaries and archived the Economy Card for {selected_date.isoformat()}.")
                        success = True # Mark as successful
                    except LibsqlError as e: # <-- NEW: Use LibsqlError
                        logger.log(f"❌ **FATAL Error:** Database error while saving: {e}")
                    finally:
                        if conn_eco_save:
                            conn_eco_save.close() # <-- NEW

            except json.JSONDecodeError:
                logger.log(f"❌ **FATAL Error:** Failed to decode the AI's response into valid JSON. The response was:")
                logger.log_code(updated_card_str, 'text')
            except Exception as e:
                logger.log(f"❌ **FATAL Error:** An error occurred while updating the economy card: {e}")
        
        if success:
            st.success(f"✅ Economy Card for {selected_date.isoformat()} updated successfully.")
            st.balloons()
        else:
            st.error(f"❌ Failed to update Economy Card for {selected_date.isoformat()}. Check log for details.")
    st.divider()

    # --- STEP 3: (REFACTORED) ---
    st.subheader("Step 3: Update Company Cards")
    
    market_news_step3, _ = get_daily_inputs(selected_date)
    if not market_news_step3:
        st.warning(f"Please complete Step 1 (Save Manual Inputs) for {selected_date.isoformat()} before running this step.")
        st.stop()
    
    # --- GUARDRAIL: Check if Economy Card exists for selected_date ---
    eco_exists = False
    try:
        # Use a quick connection to check existence
        conn_check = get_db_connection()
        if conn_check:
            rs_check = conn_check.execute("SELECT 1 FROM economy_cards WHERE date = ?", (selected_date.isoformat(),))
            if rs_check.rows:
                eco_exists = True
            conn_check.close()
    except Exception:
        pass # Fail safe implies eco_exists = False

    if not eco_exists:
        st.warning(f"Please complete Step 2 (Generate Economy Card) for {selected_date.isoformat()} before running this step.")
        st.stop()
    # -----------------------------------------------------------------
    
    st.caption("Select the tickers to process. The pipeline will run the data processor and AI update for each one.")
    
    all_db_tickers = get_all_tickers_from_db()
    
    tickers_already_done = get_all_tickers_for_archive_date(selected_date)
    
    default_tickers = [t for t in all_db_tickers if t not in tickers_already_done]
    
    selected_tickers = st.multiselect(
        "Select Tickers to Process",
        options=all_db_tickers,
        default=default_tickers
    )

    # --- NEW: Form to add tickers to the 'stocks' table ---
    with st.expander("Add Tickers to Follow"):
        with st.form("add_ticker_form"):
            st.info("If your ticker list is empty, add tickers here first. This will add them to the `stocks` table.")
            ticker_to_add = st.text_input("Ticker(s) to Add (comma-separated)", placeholder="e.g., AAPL, NVDA, SPY")
            initial_notes = st.text_area("Initial Historical Notes (Optional)", "Major Support:\nMajor Resistance:")
            
            submitted = st.form_submit_button("Add Ticker(s)")
            
            if submitted:
                tickers = [t.strip().upper() for t in ticker_to_add.split(",") if t.strip()]
                if not tickers:
                    st.warning("Please enter at least one ticker.")
                else:
                    conn_add = None
                    try:
                        conn_add = get_db_connection()
                        statements = []
                        for ticker in tickers:
                            statements.append({
                                "q": """
                                    INSERT INTO stocks (ticker, historical_level_notes) VALUES (?, ?)
                                    ON CONFLICT(ticker) DO NOTHING
                                """,
                                "args": (ticker, initial_notes)
                            })
                        
                        conn_add.batch(statements)
                        st.success(f"Successfully added/updated {len(tickers)} ticker(s)!")
                        st.info("Rerunning to refresh ticker list...")
                        st.rerun()

                    except LibsqlError as e:
                        st.error(f"Database error while adding tickers: {e}")
                    except Exception as e:
                        st.error(f"An error occurred: {e}")
                    finally:
                        if conn_add:
                            conn_add.close()
    # --- END NEW SECTION ---

    
    # --- "PRE-FLIGHT CHECK" INFO BOX ---
    st.info(
        """
        **Inputs for this step:**
        1.  **Stock Data:** Runs `generate_analysis_text` for the tickers you selected.
        2.  **Manual News:** Reads the news you saved in Step 1.
        3.  **Previous Card:** Reads the most recent card for *each ticker*.
        4.  **Historical Notes:** Reads the permanent notes for *each ticker*.
        """
    )
    
    log_container_stock = st.empty()

    if st.button(f"Run Update for {len(selected_tickers)} Ticker(s)", use_container_width=True):
        if not selected_tickers:
            st.warning("Please select at least one ticker to update.")
        else:
            log_expander_stock = log_container_stock.expander("Company Card Update Logs", expanded=True)
            logger = AppLogger(log_expander_stock)

            market_news = market_news_step3
            
            # --- MERGED STEP 3a: Run Stock Processor ---
            logger.log(f"1. Generating EOD summaries for {len(selected_tickers)} selected tickers...")
            stock_summaries_text = generate_analysis_text(selected_tickers, selected_date)
            if "Data Extraction Summary:" not in stock_summaries_text:
                logger.log(f"❌ **Error:** Failed to generate stock summaries. Processor returned: {stock_summaries_text}")
                st.stop()
            
            summaries_by_ticker = split_stock_summaries(stock_summaries_text)
            logger.log(f"   ...Successfully generated and parsed {len(summaries_by_ticker)} summaries.")
            # --- END MERGED STEP ---

            success_list = []
            failure_list = []

            with st.spinner("Running EOD updates for selected companies..."):
                # --- NEW: Get connection outside loop ---
                conn_stock_save = None
                try:
                    conn_stock_save = get_db_connection()
                    # --- MERGED STEP 3b: Run AI Loop ---
                    for ticker in selected_tickers:
                        summary = summaries_by_ticker.get(ticker)
                        if not summary:
                            logger.log(f"--- ⚠️ Skipping {ticker}: No summary was generated by the processor. ---")
                            failure_list.append(f"{ticker} (No Data)")
                            continue

                        # --- FAIL-FAST CHECK (Missing Data) ---
                        if "[ERROR]" in summary or "No data found" in summary:
                            logger.log(f"❌ **STOPPING for {ticker}:** No data found in Turso DB.")
                            logger.log("   (Skipping AI generation to prevent invalid card creation.)")
                            failure_list.append(f"{ticker} (No DB Data)")
                            continue
                        
                        logger.log(f"--- Processing {ticker}... ---")
                        
                        # --- VERIFICATION LOGGING ---
                        v_match = re.search(r"\[VERIFICATION\](.*?)(\n\n|$)", summary, re.DOTALL)
                        if v_match:
                            v_info = v_match.group(1).strip()
                            # logger.log(f"📊 [DATA STATUS] {v_info.replace(chr(10), ' | ')}") # Single line version
                            logger.log(f"📊 {v_info}") # Multi-line version for readability
                        
                        # This call uses db_utils, which is already fixed
                        previous_card_json, historical_notes, prev_card_date = get_company_card_and_notes(ticker, selected_date)
                        
                        if prev_card_date:
                            logger.log(f"   🔙 Context Loaded from: **{prev_card_date}** (Strictly previous to {selected_date})")
                        else:
                            logger.log(f"   🆕 No previous card found (New Context will be created).")
                        
                        # (Logging unchanged)
                        
                        new_card_str = update_company_card(
                            ticker=ticker,
                            previous_card_json=previous_card_json,
                            previous_card_date=prev_card_date, 
                            historical_notes=historical_notes or "",
                            new_eod_summary=summary,
                            new_eod_date=selected_date, 
                            market_context_summary=market_news,
                            logger=logger,
                            model_name=selected_model # <--- ADD THIS ARGUMENT
                        )

                        if not new_card_str:
                            logger.log(f"❌ **Error:** Failed to generate new card for {ticker}.")
                            failure_list.append(ticker)
                            continue
                        try:
                            new_card_data = json.loads(new_card_str)
                            new_card_json_formatted = json.dumps(new_card_data, indent=4)
                            
                            # --- NEW: Use connection from outside loop ---
                            # --- Save to the 'company_cards' table ---
                            conn_stock_save.execute(
                                """
                                INSERT INTO company_cards (date, ticker, raw_text_summary, company_card_json)
                                VALUES (?, ?, ?, ?)
                                ON CONFLICT(date, ticker) DO UPDATE SET
                                    raw_text_summary = excluded.raw_text_summary,
                                    company_card_json = excluded.company_card_json
                                """,
                                (selected_date.isoformat(), ticker, summary, new_card_json_formatted)
                            )
                            # No .commit() needed
                            logger.log(f"✅ **Success:** Updated and archived card for {ticker}.")
                            success_list.append(ticker)
                        except json.JSONDecodeError:
                            logger.log(f"❌ **Error:** Failed to decode AI response for {ticker}. Skipping save.")
                            failure_list.append(f"{ticker} (JSON Error)")
                        except LibsqlError as e: # <-- NEW: Use LibsqlError
                            logger.log(f"❌ **Error:** Database error for {ticker}: {e}")
                            failure_list.append(f"{ticker} (DB Error)")
                            # No .rollback() needed
                    
                    logger.log("\n--- EOD Company Update Complete! ---")
                except Exception as e:
                    logger.log(f"❌ **FATAL Error:** An unexpected error occurred during the update loop: {e}")
                finally:
                    if conn_stock_save:
                        conn_stock_save.close() # <-- NEW

            st.subheader("Update Summary")
            if success_list:
                st.success(f"✅ Successfully updated {len(success_list)} tickers: {', '.join(success_list)}")
            if failure_list:
                st.error(f"❌ Failed to update {len(failure_list)} tickers: {', '.join(failure_list)}")
                st.warning("Please check the log above for detailed errors and re-run if necessary.")
            if not failure_list and success_list:
                st.balloons()


# --- TAB 2: Card Editor (REFACTORED) ---
# ... (This tab is unchanged, as its logic was already correct) ...
with tab_editor:
    st.header("Unified Card Editor")
    st.caption("Select any date to view or edit the Economy and Company cards for that day.")
    
    archive_dates = get_all_archive_dates()
    if not archive_dates:
        st.warning("No data found. Please run the EOD pipeline at least once.")
        st.stop()

    # --- Unified Date Selector ---
    selected_archive_date_str = st.selectbox(
        "Select Date to View/Edit",
        archive_dates,
        index=0,
        key="editor_date_selector" # Add a key
    )
    
    if selected_archive_date_str:
        selected_archive_date = datetime.strptime(selected_archive_date_str, "%Y-%m-%d").date()
        st.divider()

        # --- UNIFIED ECONOMY CARD EDITOR ---
        st.subheader(f"Global Economy Card (Date: {selected_archive_date_str})")
        
        conn_eco = None
        try:
            conn_eco = get_db_connection() # <-- NEW: Gets Turso client
            
            # --- Get the specific card AND its raw summary ---
            archived_eco_card_json, raw_eco_summary = get_archived_economy_card(selected_archive_date)
            
            if not archived_eco_card_json:
                st.info(f"No economy card found for {selected_archive_date_str}.")
            else:
                try:
                    eco_card_data = json.loads(archived_eco_card_json)
                except json.JSONDecodeError:
                    st.error("Could not parse the economy card JSON.")
                    eco_card_data = json.loads(DEFAULT_ECONOMY_CARD_JSON) # Fallback
                
                if st.session_state.get('edit_mode_economy', False):
                    edited_json_string = display_editable_economy_card(eco_card_data)
                    
                    col1_eco, col2_eco = st.columns([1, 0.1])
                    with col1_eco:
                        if st.button("💾 Save Economy Card", use_container_width=True, key="save_eco_card"):
                            try:
                                json.loads(edited_json_string) # Validate
                                # --- NEW: Use client directly, no cursor ---
                                conn_eco.execute(
                                    "UPDATE economy_cards SET economy_card_json = ? WHERE date = ?",
                                    (edited_json_string, selected_archive_date.isoformat())
                                )
                                # No .commit() needed
                                st.success(f"Economy Card for {selected_archive_date_str} saved!")
                                st.session_state.edit_mode_economy = False
                                st.rerun()
                            except json.JSONDecodeError:
                                st.error("Invalid JSON format. Please correct the syntax.")
                            except Exception as e:
                                st.error(f"Error saving Economy Card: {e}")
                    with col2_eco:
                        if st.button("Cancel", use_container_width=True, key="cancel_eco_card"):
                            st.session_state.edit_mode_economy = False
                            st.rerun()
                else:
                    display_view_economy_card(eco_card_data, edit_mode_key="edit_mode_economy")

                # --- NEW: Show the raw ETF summary that was used ---
                with st.expander("View Raw ETF Summary Used to Generate This Card"):
                    st.text(raw_eco_summary or "No raw ETF summary was saved for this date.")

        except LibsqlError as e: # <-- NEW: Use LibsqlError
            st.error(f"Database error loading economy card: {e}")
        finally:
            if conn_eco:
                conn_eco.close() # <-- NEW: Close client

        st.markdown("---")
        
        # --- UNIFIED COMPANY CARD EDITOR ---
        st.subheader(f"Individual Stock Cards (Date: {selected_archive_date_str})")
        
        tickers_on_date = get_all_tickers_for_archive_date(selected_archive_date)
        
        if not tickers_on_date:
            st.info(f"No company cards found for {selected_archive_date_str}.")
        else:
            selected_ticker = st.selectbox(
                "Select Ticker to View/Edit",
                tickers_on_date,
                key='ticker_selector'
            )

            if selected_ticker:
                conn_stock = None
                try:
                    conn_stock = get_db_connection() # <-- NEW: Gets Turso client

                    # Always use the selectbox value for notes and card
                    card_json, raw_summary = get_archived_company_card(selected_archive_date, selected_ticker)
                    _, notes, _ = get_company_card_and_notes(selected_ticker, None)

                    with st.form("historical_notes_form_unified"): # Unique key
                        new_notes = st.text_area("Historical Level Notes (Major Levels)", value=notes, height=150, key=f"notes_unified_{selected_ticker}")
                        if st.form_submit_button("Save Historical Notes", use_container_width=True, key="save_notes_unified"):
                            # --- NEW: Use robust UPSERT query ---
                            conn_stock.execute(
                                """
                                INSERT INTO stocks (ticker, historical_level_notes) VALUES (?, ?)
                                ON CONFLICT(ticker) DO UPDATE SET
                                    historical_level_notes = excluded.historical_level_notes
                                """,
                                (selected_ticker, new_notes)
                            )
                            # No .commit() needed
                            st.success(f"Historical notes for {selected_ticker} saved!")
                            st.rerun()

                    st.divider()

                    if not card_json:
                        st.error(f"Could not find card for {selected_ticker} on this date.")
                    else:
                        try:
                            card_data = json.loads(card_json)
                        except json.JSONDecodeError:
                            st.error("Could not parse the company card JSON.")
                            card_data = json.loads(DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", selected_ticker))

                        if st.session_state.get('edit_mode', False):
                            edited_json_string = display_editable_market_note_card(card_data)
                            
                            col1, col2 = st.columns([1, 0.1])
                            with col1:
                                if st.button("💾 Save Company Card", use_container_width=True, key="save_company_card"):
                                    try:
                                        json.loads(edited_json_string) # Validate
                                        # --- NEW: Use client directly ---
                                        conn_stock.execute(
                                            "UPDATE company_cards SET company_card_json = ? WHERE date = ? AND ticker = ?",
                                            (edited_json_string, selected_archive_date.isoformat(), selected_ticker)
                                        )
                                        # No .commit() needed
                                        st.success(f"Card for {selected_ticker} on {selected_archive_date_str} saved!")
                                        st.session_state.edit_mode = False
                                        st.rerun()
                                    except json.JSONDecodeError:
                                        st.error("Invalid JSON format. Please correct the syntax.")
                                    except Exception as e:
                                        st.error(f"Error saving company card: {e}")
                            with col2:
                                if st.button("Cancel", use_container_width=True, key="cancel_company_card"):
                                    st.session_state.edit_mode = False
                                    st.rerun()
                        else:
                            display_view_market_note_card(card_data, edit_mode_key="edit_mode")

                        with st.expander("View Raw EOD Summary Used"):
                            st.text(raw_summary or "No raw summary was archived.")

                except LibsqlError as e: # <-- NEW: Use LibsqlError
                    st.error(f"Database error for archived card {selected_ticker}: {e}")
                finally:
                    if conn_stock:
                        conn_stock.close() # <-- NEW: Close client

                # --- PREVIOUS / NEXT BUTTONS ---
                # (This section is unchanged)
                st.divider()
                col_prev_arc, col_spacer_arc, col_next_arc = st.columns([1, 5, 1])
                
                current_idx = st.session_state.ticker_index

                def go_prev_archive():
                    new_index = current_idx - 1
                    if new_index >= 0:
                        st.session_state.ticker_index = new_index
                        st.session_state.ticker_selector = tickers_on_date[new_index]
                        st.session_state.edit_mode = False # Exit edit mode on ticker change

                def go_next_archive():
                    new_index = current_idx + 1
                    if new_index < len(tickers_on_date):
                        st.session_state.ticker_index = new_index
                        st.session_state.ticker_selector = tickers_on_date[new_index]
                        st.session_state.edit_mode = False # Exit edit mode on ticker change

                with col_prev_arc:
                    st.button(
                        "⬅️ Previous", 
                        on_click=go_prev_archive, 
                        use_container_width=True, 
                        disabled=(current_idx <= 0),
                        key="archive_prev_btn" 
                    )
                
                with col_next_arc:
                    st.button(
                        "Next ➡️", 
                        on_click=go_next_archive, 
                        use_container_width=True, 
                        disabled=(current_idx >= len(tickers_on_date) - 1),
                        key="archive_next_btn" 
                    )