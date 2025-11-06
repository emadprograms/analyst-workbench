import streamlit as st
import sqlite3
import os
import re
import json
from datetime import date, datetime, timedelta

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
    API_KEYS,
    STOCK_TICKERS,
    ETF_TICKERS,
    DEFAULT_COMPANY_OVERVIEW_JSON,
    DEFAULT_ECONOMY_CARD_JSON,
    DATABASE_FILE
)

# --- Corrected Imports ---
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
    get_db_connection,
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

# --- UI Validation Check for Gemini API Keys ---
if not API_KEYS or not isinstance(API_KEYS, list) or len(API_KEYS) == 0:
    st.error("Error: Gemini API keys not found in st.secrets.")
    # ... (rest of error message) ...
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
# 'edit_mode' and 'edit_mode_economy' are now used for the UNIFIED editor
if 'edit_mode' not in st.session_state: st.session_state['edit_mode'] = False
if 'edit_mode_economy' not in st.session_state: st.session_state['edit_mode_economy'] = False
# 'ticker_index' and 'ticker_selector' are now used for the UNIFIED editor's ticker dropdown
if 'ticker_index' not in st.session_state: st.session_state['ticker_index'] = 0
if 'ticker_selector' not in st.session_state: st.session_state['ticker_selector'] = None

if 'etf_processor_output' not in st.session_state: st.session_state['etf_processor_output'] = ""
if 'stock_processor_output' not in st.session_state: st.session_state['stock_processor_output'] = ""
if 'eod_raw_stocks' not in st.session_state: st.session_state['eod_raw_stocks'] = ""

# --- NEW: This session state is for the UNIFIED editor's date selector ---
if 'current_selected_date' not in st.session_state: st.session_state['current_selected_date'] = None


# --- Define Tabs ---
tab_runner_eod, tab_editor = st.tabs([
    "Pipeline Runner (EOD)",
    "Card Editor", # Renamed
])

# --- TAB 1: Pipeline Runner (EOD) ---
with tab_runner_eod:
    st.header("Date-Aware EOD Workflow")
    st.info(f"{len(API_KEYS)} Gemini API keys available in rotation.")

    selected_date = st.date_input(
        "Select the Date to Process",
        value=default_pipeline_date, 
        help="Defaults to the day after the last successfully processed date."
    )
    
    # --- FIX #2: "GAP DETECTION" GUARDRAIL ---
    if latest_update_date and selected_date > latest_update_date:
        day_diff = (selected_date - latest_update_date).days
        if day_diff > 1:
            # Check if the gap is *just* a weekend (e.g., Fri to Mon)
            is_just_weekend = (latest_update_date.weekday() == 4 and selected_date.weekday() == 0 and day_diff == 3)
            if not is_just_weekend:
                st.warning(
                    f"**Gap Detected:** You are about to process {selected_date.isoformat()}, "
                    f"but the last processed date was {latest_update_date.isoformat()}. "
                    f"You are skipping {day_diff - 1} day(s). Please ensure this is intentional (e.g., due to a holiday)."
                )
    # --- END FIX ---

    st.divider()

    st.subheader("Step 1: Gather & Save Daily Inputs")
    st.caption("Provide the shared market-wide inputs for the selected date. This must be done before running the analysis steps.")

    def run_etf_processor():
        with st.spinner(f"Running ETF processor for {selected_date.isoformat()}..."):
            output = generate_analysis_text(ETF_TICKERS, selected_date)
            if "Data Extraction Summary:" in output:
                st.session_state.etf_processor_output = output
                st.session_state.daily_etf_summaries = output
            else:
                st.session_state.etf_processor_output = ""
                st.session_state.daily_etf_summaries = ""
                st.error(f"Failed to generate ETF summaries. Processor returned: {output}")

    st.button(
        "▶️ Run ETF Processor & Populate Summaries",
        on_click=run_etf_processor,
        use_container_width=True
    )

    with st.form("daily_inputs_form"):
        market_news_input = st.text_area(
            "Overall Market/Company News Summary:",
            height=120,
            key="daily_market_news",
            help="A summary of the day's overall market sentiment or major news. This is saved once per day."
        )
        etf_summaries_input = st.text_area(
            "Paste ETF EOD Summaries:",
            height=200,
            key="daily_etf_summaries",
            help="Paste the text output from the processor app for key ETFs like SPY, QQQ, XLF, etc."
        )
        if st.form_submit_button("💾 Save Daily Inputs", use_container_width=True):
            if not market_news_input or not etf_summaries_input:
                st.warning("Please provide both Market News and ETF Summaries before saving.")
            else:
                if upsert_daily_inputs(selected_date, market_news_input, etf_summaries_input):
                    st.success(f"Daily inputs for {selected_date.isoformat()} saved successfully.")
                    st.rerun()
                else:
                    st.error("Failed to save daily inputs. Check logs for details.")
    st.divider()

    st.subheader("Step 2: Update Economy Card")

    # --- "GUARDRAIL" ---
    market_news_step2, _ = get_daily_inputs(selected_date)
    
    if not market_news_step2:
        st.warning(f"Please complete Step 1 (Save Daily Inputs) for {selected_date.isoformat()} before running this step.")
        st.stop()
    
    st.caption("This step will use the saved daily inputs to generate and archive the economy card.")
    
    log_container_eco = st.empty()

    if st.button("Run Economy Card EOD Update", use_container_width=True):
        log_expander_eco = log_container_eco.expander("Economy Card Update Log", expanded=True)
        logger = AppLogger(log_expander_eco)
        
        # This will hold our final result
        success = False 
        
        with st.spinner(f"Updating Economy Card for {selected_date.isoformat()}..."):
            market_news, etf_summaries = get_daily_inputs(selected_date)
            current_economy_card_json, _ = get_economy_card()
            
            if not market_news or not etf_summaries:
                logger.log(f"❌ **Error:** Could not find Daily Inputs for {selected_date.isoformat()}. Please complete Step 1 first.")
                st.stop()
            
            logger.log("1. Found Daily Inputs and most recent Economy Card.")
            
            try:
                logger.log("2. Calling AI to generate updated Economy Card...")
                
                updated_card_str = update_economy_card( 
                    current_economy_card=current_economy_card_json,
                    daily_market_news=market_news,
                    etf_summaries=etf_summaries,
                    selected_date=selected_date,
                    logger=logger 
                )

                if not updated_card_str:
                    logger.log("❌ **Error:** Failed to generate new economy card. AI service returned no data.")
                else:
                    new_card_data = json.loads(updated_card_str)
                    new_card_json = json.dumps(new_card_data, indent=4)
                    logger.log("3. Successfully generated and validated the new Economy Card.")
                    
                    try:
                        with get_db_connection() as conn:
                            conn.execute(
                                """
                                INSERT INTO economy_cards (date, economy_card_json)
                                VALUES (?, ?)
                                ON CONFLICT(date) DO UPDATE SET economy_card_json = excluded.economy_card_json
                                """,
                                (selected_date.isoformat(), new_card_json)
                            )
                            conn.commit()
                        logger.log(f"✅ **Success:** Saved and archived the Economy Card for {selected_date.isoformat()}.")
                        success = True # Mark as successful
                    except sqlite3.Error as e:
                        logger.log(f"❌ **FATAL Error:** Database error while saving the new economy card: {e}")

            except json.JSONDecodeError:
                logger.log(f"❌ **FATAL Error:** Failed to decode the AI's response into valid JSON. The response was:")
                logger.log_code(updated_card_str, 'text')
            except Exception as e:
                logger.log(f"❌ **FATAL Error:** An error occurred while updating the economy card: {e}")
        
        # --- NEW: FINAL SUMMARY REPORT ---
        if success:
            st.success(f"✅ Economy Card for {selected_date.isoformat()} updated successfully.")
            st.balloons()
        else:
            st.error(f"❌ Failed to update Economy Card for {selected_date.isoformat()}. Check log for details.")
    st.divider()

    st.subheader("Step 3: Update Company Cards")
    
    market_news_step3, _ = get_daily_inputs(selected_date)
    
    if not market_news_step3:
        st.warning(f"Please complete Step 1 (Save Daily Inputs) for {selected_date.isoformat()} before running this step.")
        st.stop() # Stop rendering the rest of this tab

    st.caption("This step will use the saved daily inputs to generate and archive company analysis.")
    def run_stock_processor():
        with st.spinner(f"Running stock processor for {selected_date.isoformat()}..."):
            output = generate_analysis_text(STOCK_TICKERS, selected_date)
            if "Data Extraction Summary:" in output:
                st.session_state.stock_processor_output = output
                st.session_state.eod_raw_stocks = output
            else:
                st.session_state.stock_processor_output = ""
                st.session_state.eod_raw_stocks = ""
                st.error(f"Failed to generate summaries. Processor returned: {output}")

    st.button(
        "▶️ Run Stock Processor & Populate Summaries",
        on_click=run_stock_processor,
        use_container_width=True
    )
    
    log_container_stock = st.empty()

    with st.form("company_update_form"):
        stock_summaries_input = st.text_area(
            "Stock EOD Summaries:",
            height=300, 
            key="eod_raw_stocks"
        )
        if st.form_submit_button("Run Stock EOD Updates", use_container_width=True):
            if not stock_summaries_input:
                st.warning("Please provide Stock EOD Summaries before running the update.")
            else:
                log_expander_stock = log_container_stock.expander("Company Card Update Logs", expanded=True)
                logger = AppLogger(log_expander_stock)

                market_news = market_news_step3
                
                summaries_by_ticker = split_stock_summaries(stock_summaries_input)
                if not summaries_by_ticker:
                    logger.log("❌ **Error:** Could not parse any tickers. Ensure the text box contains valid 'Data Extraction Summary' blocks.")
                    st.stop()
                
                logger.log(f"Found {len(summaries_by_ticker)} tickers to process: {', '.join(summaries_by_ticker.keys())}")
                
                # --- NEW: Lists to track success/failure ---
                success_list = []
                failure_list = []

                with st.spinner("Running EOD updates for all companies..."):
                    with get_db_connection() as conn:
                        try:
                            for ticker, summary in summaries_by_ticker.items():
                                logger.log(f"--- Processing {ticker}... ---")
                                
                                previous_card_json, historical_notes, prev_card_date = get_company_card_and_notes(ticker, selected_date)
                                
                                logger.log(f"  1. Found EOD Summary for {selected_date.isoformat()}.")
                                logger.log(f"  2. Found Market Context: '{market_news[:50].strip()}...'")
                                if prev_card_date:
                                    logger.log(f"  3. Found Previous Card (from {prev_card_date}).")
                                else:
                                    logger.log(f"  3. No Previous Card found. Using default template.")
                                
                                if historical_notes:
                                     logger.log(f"  4. Found Historical Notes: '{historical_notes[:50].strip()}...'")
                                else:
                                     logger.log(f"  4. No Historical Notes found for this ticker.")
                                
                                logger.log("  5. All context gathered. Calling AI...")

                                new_card_str = update_company_card(
                                    ticker=ticker,
                                    previous_card_json=previous_card_json,
                                    previous_card_date=prev_card_date, 
                                    historical_notes=historical_notes or "",
                                    new_eod_summary=summary,
                                    new_eod_date=selected_date, 
                                    market_context_summary=market_news,
                                    logger=logger 
                                )

                                if not new_card_str:
                                    logger.log(f"❌ **Error:** Failed to generate new card for {ticker}.")
                                    failure_list.append(ticker) # Add to failure list
                                    continue
                                try:
                                    new_card_data = json.loads(new_card_str)
                                    new_card_json_formatted = json.dumps(new_card_data, indent=4)
                                    cursor = conn.cursor()
                                    
                                    cursor.execute(
                                        """
                                        INSERT INTO company_cards (date, ticker, raw_text_summary, company_card_json)
                                        VALUES (?, ?, ?, ?)
                                        ON CONFLICT(date, ticker) DO UPDATE SET
                                            raw_text_summary = excluded.raw_text_summary,
                                            company_card_json = excluded.company_card_json
                                        """,
                                        (selected_date.isoformat(), ticker, summary, new_card_json_formatted)
                                    )
                                    conn.commit()
                                    logger.log(f"✅ **Success:** Updated and archived card for {ticker}.")
                                    success_list.append(ticker) # Add to success list
                                except json.JSONDecodeError:
                                    logger.log(f"❌ **Error:** Failed to decode AI response for {ticker}. Skipping save.")
                                    failure_list.append(f"{ticker} (JSON Error)")
                                except sqlite3.Error as e:
                                    logger.log(f"❌ **Error:** Database error for {ticker}: {e}")
                                    failure_list.append(f"{ticker} (DB Error)")
                                    conn.rollback()
                            
                            logger.log("\n--- EOD Company Update Complete! ---")
                        except Exception as e:
                            logger.log(f"❌ **FATAL Error:** An unexpected error occurred during the update loop: {e}")

                # --- NEW: FINAL SUMMARY REPORT ---
                st.subheader("Update Summary")
                if success_list:
                    st.success(f"✅ Successfully updated {len(success_list)} tickers: {', '.join(success_list)}")
                if failure_list:
                    st.error(f"❌ Failed to update {len(failure_list)} tickers: {', '.join(failure_list)}")
                    st.warning("Please check the log above for detailed errors and re-run if necessary.")
                if not failure_list and success_list:
                    st.balloons()
                # --- END NEW ---


# --- TAB 2: Card Editor (REFACTORED) ---
with tab_editor:
    st.header("Unified Card Editor")
    st.caption("Select any date to view or edit the Economy and Company cards for that day.")
    
    archive_dates = get_all_archive_dates()
    if not archive_dates:
        st.warning("No data found. Please run the EOD pipeline at least once.")
        st.stop()

    # --- Unified Date Selector ---
    # Default to the most recent date in the archive (index 0)
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
            conn_eco = get_db_connection()
            cursor_eco = conn_eco.cursor()
            
            # Get the card for the *specific date* selected
            archived_eco_card_json = get_archived_economy_card(selected_archive_date)
            
            if not archived_eco_card_json:
                st.info(f"No economy card found for {selected_archive_date_str}.")
            else:
                try:
                    eco_card_data = json.loads(archived_eco_card_json)
                except json.JSONDecodeError:
                    st.error("Could not parse the economy card JSON.")
                    eco_card_data = json.loads(DEFAULT_ECONOMY_CARD_JSON) # Fallback
                
                # Toggle logic for the *single* economy editor
                if st.session_state.get('edit_mode_economy', False):
                    edited_json_string = display_editable_economy_card(eco_card_data)
                    
                    col1_eco, col2_eco = st.columns([1, 0.1])
                    with col1_eco:
                        if st.button("💾 Save Economy Card", use_container_width=True, key="save_eco_card"):
                            try:
                                json.loads(edited_json_string) # Validate
                                # --- REFACTOR: Using new table 'economy_cards' ---
                                cursor_eco.execute(
                                    "UPDATE economy_cards SET economy_card_json = ? WHERE date = ?",
                                    (edited_json_string, selected_archive_date.isoformat())
                                )
                                conn_eco.commit()
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

        except sqlite3.Error as e:
            st.error(f"Database error loading economy card: {e}")
        finally:
            if conn_eco:
                conn_eco.close()

        st.markdown("---")
        
        # --- UNIFIED COMPANY CARD EDITOR ---
        st.subheader(f"Individual Stock Cards (Date: {selected_archive_date_str})")
        
        # Get all tickers that have an entry on the *selected date*
        tickers_on_date = get_all_tickers_for_archive_date(selected_archive_date)
        
        if not tickers_on_date:
            st.info(f"No company cards found for {selected_archive_date_str}.")
        else:
            # --- Ticker Selector Logic (for the selected date) ---
            
            # Reset index if the date changes
            if 'current_selected_date' not in st.session_state or st.session_state.current_selected_date != selected_archive_date_str:
                st.session_state.current_selected_date = selected_archive_date_str
                st.session_state.ticker_index = 0
                st.session_state.ticker_selector = tickers_on_date[0] if tickers_on_date else None
            
            # Sync index and selector
            try:
                if tickers_on_date and st.session_state.ticker_selector in tickers_on_date:
                    st.session_state.ticker_index = tickers_on_date.index(st.session_state.ticker_selector)
                elif tickers_on_date: # Fallback if state is out of sync
                    st.session_state.ticker_index = 0
                    st.session_state.ticker_selector = tickers_on_date[0]
            except (ValueError, IndexError):
                st.session_state.ticker_index = 0
                st.session_state.ticker_selector = tickers_on_date[0] if tickers_on_date else None

            selected_ticker = st.selectbox(
                "Select Ticker to View/Edit",
                tickers_on_date,
                index=st.session_state.ticker_index,
                key='ticker_selector' 
            )
            
            if selected_ticker:
                conn_stock = None
                try:
                    conn_stock = get_db_connection()
                    cursor_stock = conn_stock.cursor()
                    
                    # Get the specific archived card and the *living* historical notes
                    card_json, raw_summary = get_archived_company_card(selected_archive_date, selected_ticker)
                    # This function just gets the notes from the 'stocks' table
                    _, notes, _ = get_company_card_and_notes(selected_ticker, None) # Pass None to just get notes

                    with st.form("historical_notes_form_unified"): # Unique key
                        new_notes = st.text_area("Historical Level Notes (Major Levels)", value=notes, height=150, key="notes_unified")
                        if st.form_submit_button("Save Historical Notes", use_container_width=True, key="save_notes_unified"):
                            cursor_stock.execute("UPDATE stocks SET historical_level_notes = ? WHERE ticker = ?", (new_notes, selected_ticker))
                            if cursor_stock.rowcount == 0:
                                cursor_stock.execute("INSERT INTO stocks (ticker, historical_level_notes) VALUES (?, ?)", (selected_ticker, new_notes))
                            conn_stock.commit()
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
                                        # --- REFACTOR: Using new table 'company_cards' ---
                                        cursor_stock.execute(
                                            "UPDATE company_cards SET company_card_json = ? WHERE date = ? AND ticker = ?",
                                            (edited_json_string, selected_archive_date.isoformat(), selected_ticker)
                                        )
                                        conn_stock.commit()
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

                except sqlite3.Error as e:
                    st.error(f"Database error for archived card {selected_ticker}: {e}")
                finally:
                    if conn_stock:
                        conn_stock.close()

                # --- PREVIOUS / NEXT BUTTONS ---
                st.divider()
                col_prev_arc, col_spacer_arc, col_next_arc = st.columns([1, 5, 1])
                
                current_idx = st.session_state.ticker_index

                def go_prev_archive():
                    new_index = current_idx - 1
                    if new_index >= 0:
                        st.session_state.ticker_selector = tickers_on_date[new_index]
                        st.session_state.edit_mode = False # Exit edit mode on ticker change

                def go_next_archive():
                    new_index = current_idx + 1
                    if new_index < len(tickers_on_date):
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