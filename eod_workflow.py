import streamlit as st
import sqlite3
import os
import re
import json
import time
import random
from datetime import date, datetime, timedelta

try:
    from pytz import timezone as pytz_timezone
    US_EASTERN = pytz_timezone('US/Eastern')
except ImportError:
    st.warning("`pytz` library not found. Using basic timezone info.")
    US_EASTERN = timezone(timedelta(hours=-5))

# --- Local Imports ---
from modules.config import (
    API_KEYS,
    STOCK_TICKERS,
    ETF_TICKERS,
    DEFAULT_COMPANY_OVERVIEW_JSON,
    DEFAULT_ECONOMY_CARD_JSON,
    DATABASE_FILE
)
from modules.data_processing import generate_analysis_text, parse_raw_summary
from modules.db_utils import get_all_tickers_from_db
from modules.ui_components import (
    AppLogger,
    display_view_market_note_card,
    display_editable_market_note_card,
    display_view_economy_card,
    display_editable_economy_card
)
from modules.ai_services import update_stock_note, update_economy_card


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

# --- Define Tabs ---
tab_runner_eod, tab_editor = st.tabs([
    "Pipeline Runner (EOD)",
    "Context & EOD Card Editor",
])

# --- TAB 1: Pipeline Runner (EOD) ---
with tab_runner_eod:
    st.header("Pipeline Runner (EOD Update)")
    st.caption("Run EOD updates for individual stocks and the global economy card.")
    st.info(f"{len(API_KEYS)} Gemini API keys available in rotation.")

    col_stocks, col_economy = st.columns(2)

    # --- Column 1: Individual Stock Updates ---
    with col_stocks:
        st.subheader("1. Individual Stock Updates")
        st.caption("`INPUT:` EOD Summary Text + Previous Day's Company Card + Historical Notes")

        if st.button("▶️ Run Stock Processor", use_container_width=True, help="Runs the processor for all stocks and populates the text area below."):
            with st.spinner("Running stock processor... This may take a few minutes."):
                analysis_date_dt = datetime.now(US_EASTERN) - timedelta(days=1)
                analysis_date = analysis_date_dt.date()
                output = generate_analysis_text(STOCK_TICKERS, analysis_date)
                st.session_state.eod_raw_stocks = output
            st.rerun()

        macro_context_input = st.text_area("Overall Market/Company News Summary:", height=120, key="eod_macro_context", help="A summary of the day's overall market sentiment or major news affecting your universe of stocks. This context will be given to the AI for EACH stock update.")
        
        st.text_area("Stock EOD Summaries:", height=300, key="eod_raw_stocks")

        if st.button("Run Stock EOD Updates", use_container_width=True, key="run_eod_stocks"):
            if not st.session_state.get('eod_raw_stocks'):
                st.warning("Stock summary text is empty. Run the processor first.")
            elif not os.path.exists(DATABASE_FILE):
                st.error("Database file not found.")
            else:
                summaries = re.split(r"(Summary:\s*[\w.-]+\s*\|)", st.session_state.eod_raw_stocks)
                processed = []
                if len(summaries) > 1 and not summaries[0].strip().startswith("Summary:"):
                    if summaries[0].strip(): st.warning("Ignoring text before first summary.")
                    summaries = summaries[1:]
                for i in range(0, len(summaries), 2):
                    if i + 1 < len(summaries): processed.append(summaries[i] + summaries[i+1])
                
                if not processed:
                    st.warning("No valid stock summaries found.")
                else:
                    st.success(f"Found {len(processed)} stock summaries.")
                    logs_stocks = st.expander("Stock Update Logs", True)
                    logger_stocks = AppLogger(logs_stocks)
                    t_start = time.time()
                    for i, s in enumerate(processed):
                        key = random.choice(API_KEYS)
                        ticker = parse_raw_summary(s).get('ticker')
                        if not ticker:
                            logger_stocks.log(f"SKIP: Could not parse ticker from summary: {s[:100]}...")
                            continue
                        try:
                            update_stock_note(ticker, s, macro_context_input, key, logger_stocks)
                        except Exception as e:
                            logger_stocks.log(f"!!! EOD ERROR for {ticker}: {e}")
                        if i < len(processed) - 1:
                            logger_stocks.log("   ...waiting 1s...")
                            time.sleep(1)
                    t_end = time.time()
                    logger_stocks.log(f"--- Stock EOD Updates Done (Total Time: {t_end - t_start:.2f}s) ---")
                    st.info("Stock updates complete.")

    # --- Column 2: Global Economy Card Update ---
    with col_economy:
        st.subheader("2. Global Economy Card Update")
        st.caption("`INPUT:` Manual Macro Summary + ETF/Inter-Market EOD Summaries + Previous Day's Economy Card")
        
        if st.button("▶️ Run ETF Processor", use_container_width=True, help="Runs the processor for all ETFs and populates the text area below."):
            with st.spinner("Running ETF processor... This may take a few minutes."):
                analysis_date_dt = datetime.now(US_EASTERN) - timedelta(days=1)
                analysis_date = analysis_date_dt.date()
                output = generate_analysis_text(ETF_TICKERS, analysis_date)
                st.session_state.eod_raw_etfs = output
            st.rerun()

        manual_macro_summary = st.text_area("Your Manual Daily Macro Summary:", height=100, key="eod_manual_macro", help="Your high-level take on the day's market action and news.")
        
        st.text_area("Paste ETF EOD Summaries:", height=200, key="eod_raw_etfs", help="Paste the text output from the processor app for key ETFs like SPY, QQQ, XLF, etc.")
        
        if st.button("Run Economy Card EOD Update", use_container_width=True, key="run_eod_economy"):
            if not manual_macro_summary or not st.session_state.get('eod_raw_etfs'):
                st.warning("Please provide both a manual summary and ETF summaries.")
            elif not os.path.exists(DATABASE_FILE):
                st.error("Database file not found.")
            else:
                logs_economy = st.expander("Economy Card Update Logs", True)
                logger_economy = AppLogger(logs_economy)
                key_eco = random.choice(API_KEYS)
                with st.spinner("Updating Economy Card..."):
                    try:
                        update_economy_card(manual_macro_summary, st.session_state.eod_raw_etfs, key_eco, logger_economy)
                        st.success("Economy Card update process finished.")
                    except Exception as e:
                        logger_economy.log(f"!!! ECONOMY CARD EOD ERROR: {e}")
                        st.error("An error occurred during the Economy Card update.")

# --- TAB 2: Context & EOD Card Editor ---
with tab_editor:
    st.header("Context & EOD Card Editor")
    st.caption("Set `Historical Notes` & review/edit EOD Cards.")

    # --- Economy Card Editor ---
    st.markdown("---")
    st.subheader("Global Economy Card")
    st.caption("This is the single, global context card for the entire market.")
    
    conn_eco = None
    try:
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

        # Ensure that economy_card_data is always a dictionary
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
                        # Validate and save the JSON
                        json.loads(edited_json_string) # Will raise error if invalid
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
            display_view_economy_card(economy_card_data)

    except sqlite3.Error as e:
        st.error(f"Database error loading economy card: {e}")
    finally:
        if conn_eco:
            conn_eco.close()

    # --- Individual Stock Cards ---
    st.markdown("---")
    st.subheader("Individual Stock Cards")
    if not os.path.exists(DATABASE_FILE):
        st.error(f"Database not found at {DATABASE_FILE}")
    else:
        all_tickers = get_all_tickers_from_db()
        
        if not all_tickers:
            st.warning("No tickers found in the database.")
        else:
            # --- Session State and Navigation Logic ---
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
                    st.stop()

                if st.session_state.get('edit_mode', False):
                    edited_json_string = display_editable_market_note_card(card_data)
                    
                    col1, col2 = st.columns([1, 0.1])
                    with col1:
                        if st.button("💾 Save Company Card", use_container_width=True, key="save_company_card"):
                            try:
                                # Validate and save the JSON
                                json.loads(edited_json_string) # Will raise error if invalid
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
