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
    get_economy_card,
    get_company_card_and_notes,
    # --- New Imports for Archive Viewer ---
    get_all_archive_dates,
    get_all_tickers_for_archive_date,
    get_archived_economy_card,
    get_archived_company_card
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
    st.info("Please add your Gemini API keys to your `.streamlit/secrets.toml` file in a list format:")
    st.code('''
[gemini]
api_keys = [
    "AIzaSy...key1",
    "AIzaSy...key2",
]
    ''')
    st.stop()

# --- Initialize session state ---
if 'edit_mode' not in st.session_state: st.session_state['edit_mode'] = False
if 'edit_mode_economy' not in st.session_state: st.session_state['edit_mode_economy'] = False
if 'ticker_index' not in st.session_state: st.session_state['ticker_index'] = 0
if 'ticker_selector' not in st.session_state: st.session_state['ticker_selector'] = None
if 'etf_processor_output' not in st.session_state: st.session_state['etf_processor_output'] = ""
if 'stock_processor_output' not in st.session_state: st.session_state['stock_processor_output'] = ""
if 'eod_raw_stocks' not in st.session_state: st.session_state['eod_raw_stocks'] = ""

# --- Define Tabs ---
tab_runner_eod, tab_editor = st.tabs([
    "Pipeline Runner (EOD)",
    "Context & EOD Card Editor",
])

# --- TAB 1: Pipeline Runner (EOD) ---
with tab_runner_eod:
    st.header("Date-Aware EOD Workflow")
    st.info(f"{len(API_KEYS)} Gemini API keys available in rotation.")

    # --- MASTER DATE SELECTOR ---
    selected_date = st.date_input(
        "Select the Date to Process",
        value=datetime.now(US_EASTERN).date() - timedelta(days=1),
        help="All operations on this tab will be performed for the selected date."
    )
    st.divider()

    # --- STEP 1: GATHER AND SAVE DAILY INPUTS ---
    st.subheader("Step 1: Gather & Save Daily Inputs")
    st.caption("Provide the shared market-wide inputs for the selected date. This must be done before running the analysis steps.")

    def run_etf_processor():
        """Callback to run the ETF processor and update session state."""
        with st.spinner(f"Running ETF processor for {selected_date.isoformat()}..."):
            output = generate_analysis_text(ETF_TICKERS, selected_date)
            # --- Check for valid output ---
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
                else:
                    st.error("Failed to save daily inputs. Check logs for details.")

    st.divider()

    # --- STEP 2: UPDATE ECONOMY CARD ---
    st.subheader("Step 2: Update Economy Card")
    st.caption("This step will use the saved daily inputs to generate and archive the economy card.")

    if st.button("Run Economy Card EOD Update", use_container_width=True):
        with st.spinner(f"Updating Economy Card for {selected_date.isoformat()}..."):
            # 1. Fetch required data
            market_news, etf_summaries = get_daily_inputs(selected_date)
            current_economy_card_json = get_economy_card()

            if not market_news or not etf_summaries:
                st.error(f"Could not find Daily Inputs for {selected_date.isoformat()}. Please complete Step 1 first.")
                st.stop()
            
            if not current_economy_card_json:
                st.warning("Could not find the current Economy Card in the database. Using default.")
                current_economy_card_json = DEFAULT_ECONOMY_CARD_JSON

            # 2. Call AI service to update the card
            try:
                st.info("Generating updated Economy Card... (This may take a moment)")
                updated_card_str = update_economy_card( 
                    current_economy_card=current_economy_card_json,
                    daily_market_news=market_news,
                    etf_summaries=etf_summaries
                )
                
                if not updated_card_str:
                    st.error("Failed to generate new economy card. AI service returned no data. Check logs.")
                    st.stop()

                # 3. Validate and parse the response
                new_card_data = json.loads(updated_card_str)
                new_card_json = json.dumps(new_card_data, indent=4)
                st.success("Successfully generated and validated the new Economy Card.")

            except json.JSONDecodeError:
                st.error("Failed to decode the AI's response into valid JSON. The response was:")
                st.code(updated_card_str)
                st.stop()
            except Exception as e:
                st.error(f"An error occurred while updating the economy card: {e}")
                st.stop()

            # 4. Save the new card to the database (archive and living document)
            try:
                conn = sqlite3.connect(DATABASE_FILE)
                cursor = conn.cursor()

                # Update the "living" document
                cursor.execute(
                    "UPDATE market_context SET economy_card_json = ?, last_updated = ? WHERE context_id = 1",
                    (new_card_json, selected_date.isoformat())
                )

                # Archive the new card for that day
                cursor.execute(
                    """
                    INSERT INTO economy_card_archive (date, economy_card_json)
                    VALUES (?, ?)
                    ON CONFLICT(date) DO UPDATE SET economy_card_json = excluded.economy_card_json
                    """,
                    (selected_date.isoformat(), new_card_json)
                )
                
                conn.commit()
                st.success(f"Successfully saved and archived the Economy Card for {selected_date.isoformat()}.")

            except sqlite3.Error as e:
                st.error(f"Database error while saving the new economy card: {e}")
            finally:
                if conn:
                    conn.close()


    st.divider()

    # --- STEP 3: UPDATE COMPANY CARDS ---
    st.subheader("Step 3: Update Company Cards")
    st.caption("This step will use the saved daily inputs to generate and archive company analysis.")

    def run_stock_processor():
        """Callback to run the stock processor and update session state."""
        with st.spinner(f"Running stock processor for {selected_date.isoformat()}..."):
            output = generate_analysis_text(STOCK_TICKERS, selected_date)
            
            # --- FIX: Check if the output is valid data or an error message ---
            if "Data Extraction Summary:" in output:
                st.session_state.stock_processor_output = output
                st.session_state.eod_raw_stocks = output
            else:
                st.session_state.stock_processor_output = ""
                st.session_state.eod_raw_stocks = ""
                st.error(f"Failed to generate summaries. Processor returned: {output}")
            # -------------------------------------------------------------------


    st.button(
        "▶️ Run Stock Processor & Populate Summaries",
        on_click=run_stock_processor,
        use_container_width=True
    )

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
                with st.spinner("Running EOD updates for all companies..."):
                    # 1. Get shared market context for the day
                    market_news, _ = get_daily_inputs(selected_date)
                    if not market_news:
                        st.error(f"Market context for {selected_date.isoformat()} not found. Please complete Step 1.")
                        st.stop()

                    # 2. Parse the raw text into individual summaries
                    summaries_by_ticker = split_stock_summaries(stock_summaries_input)
                    if not summaries_by_ticker:
                        # --- FIX: Updated error message ---
                        st.error("Could not parse any tickers. Ensure the text box contains valid 'Data Extraction Summary' blocks.")
                        # --------------------------------
                        st.stop()
                    
                    st.info(f"Found {len(summaries_by_ticker)} tickers to process: {', '.join(summaries_by_ticker.keys())}")
                    
                    conn = sqlite3.connect(DATABASE_FILE)
                    try:
                        for ticker, summary in summaries_by_ticker.items():
                            with st.spinner(f"Processing {ticker}..."):
                                # 3. Get current card and notes for the ticker
                                previous_card_json, historical_notes = get_company_card_and_notes(ticker)
                                if previous_card_json is None:
                                    previous_card_json = DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", ticker)
                                    st.write(f"No existing card for {ticker}, using default.")

                                # 4. Call AI service to get the updated card
                                new_card_str = update_company_card(
                                    ticker=ticker,
                                    previous_card_json=previous_card_json,
                                    historical_notes=historical_notes or "",
                                    new_eod_summary=summary,
                                    market_context_summary=market_news
                                )

                                if not new_card_str:
                                    st.error(f"Failed to generate new card for {ticker}. See logs for details.")
                                    continue

                                # 5. Validate and save the new card
                                try:
                                    new_card_data = json.loads(new_card_str)
                                    new_card_json_formatted = json.dumps(new_card_data, indent=4)

                                    cursor = conn.cursor()
                                    # Update the "living" document
                                    cursor.execute(
                                        "UPDATE stocks SET company_overview_card_json = ?, last_updated = ? WHERE ticker = ?",
                                        (new_card_json_formatted, selected_date.isoformat(), ticker)
                                    )
                                    # If no row was updated, it means the ticker doesn't exist yet.
                                    if cursor.rowcount == 0:
                                        cursor.execute(
                                            "INSERT INTO stocks (ticker, company_overview_card_json, last_updated) VALUES (?, ?, ?)",
                                            (ticker, new_card_json_formatted, selected_date.isoformat())
                                        )

                                    # Archive the new card
                                    cursor.execute(
                                        """
                                        INSERT INTO company_card_archive (date, ticker, raw_text_summary, company_card_json)
                                        VALUES (?, ?, ?, ?)
                                        ON CONFLICT(date, ticker) DO UPDATE SET
                                            raw_text_summary = excluded.raw_text_summary,
                                            company_card_json = excluded.company_card_json
                                        """,
                                        (selected_date.isoformat(), ticker, summary, new_card_json_formatted)
                                    )
                                    conn.commit()
                                    st.success(f"Successfully updated and archived card for {ticker}.")

                                except json.JSONDecodeError:
                                    st.error(f"Failed to decode AI response for {ticker}. Skipping save.")
                                except sqlite3.Error as e:
                                    st.error(f"Database error for {ticker}: {e}")
                                    conn.rollback()
                        
                        st.balloons()
                        st.header("EOD Company Update Complete!")

                    finally:
                        if conn:
                            conn.close()

# --- TAB 2: Context & EOD Card Editor ---
with tab_editor:
    st.header("Context & EOD Card Editor")
    
    view_mode = st.radio(
        "Select View",
        ["View Living Cards (Editable)", "View Archived Cards (Read-Only)"],
        horizontal=True,
        label_visibility="collapsed"
    )
    
    st.divider()

    # --- OPTION 1: "LIVING" CARD EDITOR (Your original code) ---
    if view_mode == "View Living Cards (Editable)":
        
        # --- Economy Card Editor ---
        st.subheader("Global Economy Card")
        st.caption("This is the single, global context card for the entire market.")
        
        conn_eco = None
        try:
            # --- FIX: Connect read-write for editing ---
            conn_eco = sqlite3.connect(DATABASE_FILE) 
            cursor_eco = conn_eco.cursor()
            cursor_eco.execute("SELECT economy_card_json FROM market_context WHERE context_id = 1")
            eco_data_row = cursor_eco.fetchone()
            
            card_json_str = DEFAULT_ECONOMY_CARD_JSON
            if eco_data_row and eco_data_row[0]:
                card_json_str = eco_data_row[0]

            try:
                economy_card_data = json.loads(card_json_str)
            except json.JSONDecodeError:
                st.error("Could not decode the Global Economy Card JSON. Please fix it below or save a valid default.")
                economy_card_data = json.loads(DEFAULT_ECONOMY_CARD_JSON)

            if isinstance(economy_card_data, str):
                try:
                    economy_card_data = json.loads(economy_card_data)
                except json.JSONDecodeError:
                    st.error("Failed to parse economy card data string into a dictionary. Resetting to default.")
                    economy_card_data = json.loads(DEFAULT_ECONOMY_CARD_JSON)

            if st.session_state.get('edit_mode_economy', False):
                edited_json_string = display_editable_economy_card(economy_card_data)
                
                col1_eco, col2_eco = st.columns([1, 0.1])
                with col1_eco:
                    if st.button("💾 Save Economy Card", use_container_width=True, key="save_eco_card"):
                        try:
                            json.loads(edited_json_string) 
                            cursor_eco.execute("UPDATE market_context SET economy_card_json = ?, last_updated = ? WHERE context_id = 1", (edited_json_string, date.today().isoformat()))
                            conn_eco.commit()
                            st.success("Global Economy Card saved!")
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
                # --- THIS CALL IS UNCHANGED (Default show_edit_button=True) ---
                display_view_economy_card(economy_card_data)

        except sqlite3.Error as e:
            st.error(f"Database error loading economy card: {e}")
        finally:
            if conn_eco:
                conn_eco.close()

        # --- Individual Stock Cards Editor ---
        st.markdown("---")
        st.subheader("Individual Stock Cards")
        if not os.path.exists(DATABASE_FILE):
            st.error(f"Database not found at {DATABASE_FILE}")
        else:
            all_tickers = get_all_tickers_from_db()
            
            if not all_tickers:
                st.warning("No tickers found in the database.")
            else:
                if 'ticker_index' not in st.session_state:
                    st.session_state.ticker_index = 0
                if 'ticker_selector' not in st.session_state or st.session_state.ticker_selector is None:
                    st.session_state.ticker_selector = all_tickers[st.session_state.ticker_index] if all_tickers else None

                try:
                    if all_tickers and st.session_state.ticker_selector in all_tickers:
                        st.session_state.ticker_index = all_tickers.index(st.session_state.ticker_selector)
                    elif all_tickers:
                         st.session_state.ticker_index = 0
                         st.session_state.ticker_selector = all_tickers[0]
                except (ValueError, IndexError):
                    st.session_state.ticker_index = 0
                    if all_tickers:
                        st.session_state.ticker_selector = all_tickers[0]

                selected_ticker = st.selectbox(
                    "Select Ticker to View/Edit",
                    all_tickers,
                    index=st.session_state.ticker_index,
                    key='ticker_selector'
                )

                conn_stock = None
                try:
                    # --- FIX: Connect read-write for editing ---
                    conn_stock = sqlite3.connect(DATABASE_FILE)
                    cursor_stock = conn_stock.cursor()
                    cursor_stock.execute("SELECT historical_level_notes, company_overview_card_json FROM stocks WHERE ticker = ?", (selected_ticker,))
                    stock_data = cursor_stock.fetchone()

                    notes = stock_data[0] if stock_data else ""
                    card_json = stock_data[1] if stock_data and stock_data[1] else DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", selected_ticker)

                    with st.form("historical_notes_form"):
                        new_notes = st.text_area("Historical Level Notes (Major Levels)", value=notes, height=150)
                        if st.form_submit_button("Save Historical Notes", use_container_width=True):
                            cursor_stock.execute("UPDATE stocks SET historical_level_notes = ? WHERE ticker = ?", (new_notes, selected_ticker))
                            if cursor_stock.rowcount == 0:
                                cursor_stock.execute("INSERT INTO stocks (ticker, historical_level_notes) VALUES (?, ?)", (selected_ticker, new_notes))
                            conn_stock.commit()
                            st.success(f"Historical notes for {selected_ticker} saved!")
                            st.rerun()

                    st.divider()
                    
                    try:
                        card_data = json.loads(card_json)
                    except json.JSONDecodeError:
                        st.error("Could not decode the company overview card JSON. Displaying raw text.")
                        st.code(card_json)
                        card_data = json.loads(DEFAULT_COMPANY_OVERVIEW_JSON.replace("TICKER", selected_ticker))


                    if st.session_state.get('edit_mode', False):
                        edited_json_string = display_editable_market_note_card(card_data)
                        
                        col1, col2 = st.columns([1, 0.1])
                        with col1:
                            if st.button("💾 Save Company Card", use_container_width=True, key="save_company_card"):
                                try:
                                    json.loads(edited_json_string)
                                    cursor_stock.execute("UPDATE stocks SET company_overview_card_json = ?, last_updated = ? WHERE ticker = ?", (edited_json_string, date.today().isoformat(), selected_ticker))
                                    conn_stock.commit()
                                    st.success(f"Company card for {selected_ticker} saved!")
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
                        # --- THIS CALL IS UNCHANGED (Default show_edit_button=True) ---
                        display_view_market_note_card(card_data)

                    # --- Previous/Next Buttons ---
                    st.divider()
                    col_prev, col_spacer, col_next = st.columns([1, 5, 1])

                    def go_prev():
                        new_index = st.session_state.ticker_index - 1
                        if new_index >= 0:
                            st.session_state.ticker_selector = all_tickers[new_index]

                    def go_next():
                        new_index = st.session_state.ticker_index + 1
                        if new_index < len(all_tickers):
                            st.session_state.ticker_selector = all_tickers[new_index]

                    with col_prev:
                        st.button("⬅️ Previous", on_click=go_prev, use_container_width=True, disabled=(st.session_state.ticker_index <= 0))
                    
                    with col_next:
                        st.button("Next ➡️", on_click=go_next, use_container_width=True, disabled=(st.session_state.ticker_index >= len(all_tickers) - 1))

                except sqlite3.Error as e:
                    st.error(f"Database error for {selected_ticker}: {e}")
                finally:
                    if conn_stock:
                        conn_stock.close()

    # --- OPTION 2: "ARCHIVE" CARD BROWSER (New functionality) ---
    elif view_mode == "View Archived Cards (Read-Only)":
        
        st.subheader("Archived Card Browser")
        archive_dates = get_all_archive_dates()
        
        if not archive_dates:
            st.warning("No archived data found.")
        else:
            # --- FIX: Default to index 0 (most recent) ---
            selected_archive_date_str = st.selectbox(
                "Select Date to Review",
                archive_dates,
                index=0 # Default to the most recent archive date
            )
            
            if selected_archive_date_str:
                selected_archive_date = datetime.strptime(selected_archive_date_str, "%Y-%m-%d").date()
                st.markdown("---")
                
                # --- Display Archived Economy Card ---
                st.subheader(f"Global Economy Card (Archived: {selected_archive_date_str})")
                archived_eco_card_json = get_archived_economy_card(selected_archive_date)
                
                if not archived_eco_card_json:
                    st.info("No archived economy card found for this date.")
                else:
                    try:
                        eco_card_data = json.loads(archived_eco_card_json)
                        
                        # --- THIS IS THE FIX ---
                        # Pass show_edit_button=False to hide the pencil
                        display_view_economy_card(eco_card_data, show_edit_button=False)
                        
                    except json.JSONDecodeError:
                        st.error("Could not parse the archived economy card JSON.")
                        st.code(archived_eco_card_json, language="json")

                st.markdown("---")
                
                # --- Display Archived Company Cards ---
                st.subheader(f"Individual Stock Cards (Archived: {selected_archive_date_str})")
                tickers_on_date = get_all_tickers_for_archive_date(selected_archive_date)
                
                if not tickers_on_date:
                    st.info("No archived company cards found for this date.")
                else:
                    selected_archive_ticker = st.selectbox(
                        "Select Ticker to Review",
                        tickers_on_date
                    )
                    
                    if selected_archive_ticker:
                        card_json, raw_summary = get_archived_company_card(selected_archive_date, selected_archive_ticker)
                        
                        if not card_json:
                            st.error(f"Could not find archived card for {selected_archive_ticker} on this date.")
                        else:
                            try:
                                card_data = json.loads(card_json)
                                
                                # --- THIS IS THE FIX ---
                                # Pass show_edit_button=False to hide the pencil
                                display_view_market_note_card(card_data, show_edit_button=False)
                                
                                # Show the raw summary that generated this card
                                with st.expander("View Raw EOD Summary Used"):
                                    st.text(raw_summary or "No raw summary was archived.")
                                    
                            except json.JSONDecodeError:
                                st.error("Could not parse the archived company card JSON.")
                                st.code(card_json, language="json")