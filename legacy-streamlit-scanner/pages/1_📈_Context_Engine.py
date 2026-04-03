import streamlit as st
import pandas as pd
import json
import time
import re
from datetime import datetime, timezone, timedelta

# ==============================================================================
# CONFIGURATION
# ==============================================================================
st.set_page_config(page_title="Pre-Market Analyst (Context Engine)", layout="wide")

CORE_INTERMARKET_TICKERS = [
    "BTCUSDT", "CL=F", "DIA", "EURUSDT", "IWM",
    "PAXGUSDT", "QQQ", "SMH", "SPY", "TLT",
    "UUP", "XLC", "XLF", "XLI", "XLP",
    "XLU", "XLV", "NDAQ", "^VIX"
]

# ==============================================================================
# LOCAL IMPORTS
# ==============================================================================
try:
    from modules.key_manager import KeyManager
    from modules.utils import AppLogger, get_turso_credentials
    from modules.database import (
        get_db_connection,
        init_db_schema,
        get_latest_economy_card_date,
        get_eod_economy_card,
    )
    from modules.processing import (
        get_latest_price_details,
        get_session_bars_from_db,
        analyze_market_context,      
        get_previous_session_stats   
    )
    from modules.gemini import call_gemini_with_rotation, AVAILABLE_MODELS
    from modules.ui import (
        render_sidebar,
        render_main_content,
        render_proximity_scan,
        render_battle_commander,
    )
except ImportError as e:
    st.error(f"❌ CRITICAL MISSING FILE: {e}")
    st.stop()

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def reset_application_state():
    """
    Clears all data-related session state variables to reset the page 
    as if nothing has been run yet.
    """
    keys_to_reset = [
        'premarket_economy_card', 
        'latest_macro_date', 
        'proximity_scan_results',
        'curated_tickers', 
        'final_briefing', 
        'xray_snapshot', 
        'glassbox_eod_card', 
        'glassbox_etf_data', 
        'glassbox_prompt', 
        'audit_logs'
    ]
    for key in keys_to_reset:
        if key in st.session_state:
            del st.session_state[key]
    
    # Provide visual feedback
    st.toast("Configuration Changed - System Reset", icon="🔄")

# ==============================================================================
# MAIN APPLICATION LOGIC
# ==============================================================================

def main():
    st.title("Pre-Market Context Engine (Impact & Migration)")

    # --- Session State Initialization ---
    if 'premarket_economy_card' not in st.session_state: st.session_state.premarket_economy_card = None
    if 'latest_macro_date' not in st.session_state: st.session_state.latest_macro_date = None
    if 'proximity_scan_results' not in st.session_state: st.session_state.proximity_scan_results = []
    if 'curated_tickers' not in st.session_state: st.session_state.curated_tickers = []
    if 'final_briefing' not in st.session_state: st.session_state.final_briefing = None
    if 'xray_snapshot' not in st.session_state: st.session_state.xray_snapshot = None
    if 'app_logger' not in st.session_state: st.session_state.app_logger = AppLogger(None)

    if 'glassbox_eod_card' not in st.session_state: st.session_state.glassbox_eod_card = None
    if 'glassbox_etf_data' not in st.session_state: st.session_state.glassbox_etf_data = []
    if 'glassbox_prompt' not in st.session_state: st.session_state.glassbox_prompt = None
    if 'utc_timezone' not in st.session_state: st.session_state.utc_timezone = timezone.utc

    # --- Startup ---
    startup_logger = st.session_state.app_logger
    db_url, auth_token = get_turso_credentials()
    turso = get_db_connection(db_url, auth_token)
    if turso:
        init_db_schema(turso, startup_logger)
    else:
        st.error("DB Connection Failed.")
        st.stop()

    if 'key_manager_instance' not in st.session_state:
        st.session_state.key_manager_instance = KeyManager(db_url=db_url, auth_token=auth_token)

    # --- Render Sidebar & Capture Config ---
    selected_model, mode, simulation_cutoff_dt, simulation_cutoff_str = render_sidebar(AVAILABLE_MODELS)

    # --- STATE MANAGEMENT: RESET ON CONFIG CHANGE ---
    if mode == "Simulation":
        current_config_signature = (selected_model, mode, simulation_cutoff_str)
    else:
        current_config_signature = (selected_model, mode)

    # Check against history
    if 'last_config_signature' not in st.session_state:
        st.session_state.last_config_signature = current_config_signature
    
    if st.session_state.last_config_signature != current_config_signature:
        reset_application_state()
        st.session_state.last_config_signature = current_config_signature
        st.rerun() # Force immediate UI refresh to clear old data

    # --- Main Content ---
    tab1, tab2 = st.tabs(["Step 1: Context Monitor", "Step 2: Head Trader"])
    logger = st.session_state.app_logger

    # --- TAB 1: CONTEXT MONITOR ---
    with tab1:
        etf_placeholder = st.empty()
        pm_news, eod_placeholder, prompt_placeholder = render_main_content(mode, simulation_cutoff_dt, etf_placeholder)

        if st.button("Run Context Engine (Step 0)", key="btn_step0", type="primary"):
            st.session_state.glassbox_etf_data = []
            etf_placeholder.empty()
            etf_json_cards = [] # Store full JSONs for AI context

            with st.status(f"Running Impact Analysis ({mode})...", expanded=True) as status:
                status.write("Fetching EOD Card...")
                
                # EOD Logic (Simulation aware)
                if mode == "Simulation": 
                    eod_search_date = (simulation_cutoff_dt - timedelta(days=1)).strftime('%Y-%m-%d')
                else: 
                    eod_search_date = simulation_cutoff_dt.strftime('%Y-%m-%d')

                latest_date = get_latest_economy_card_date(turso, simulation_cutoff_str, logger)
                eod_card = {}
                if latest_date:
                    data = get_eod_economy_card(turso, latest_date, logger)
                    if data: eod_card = data
                
                st.session_state.glassbox_eod_card = eod_card
                eod_placeholder.json(eod_card, expanded=False)

                status.write("Scanning Tickers (Impact Engine)...")
                benchmark_date_str = st.session_state.analysis_date.isoformat()

                for epic in CORE_INTERMARKET_TICKERS:
                    latest_price, price_ts = get_latest_price_details(turso, epic, simulation_cutoff_str, logger)
                    
                    if latest_price:
                        # 1. Get Bars
                        df = get_session_bars_from_db(turso, epic, benchmark_date_str, simulation_cutoff_str, logger)
                        
                        # 2. Get Yesterday's Stats (Context)
                        ref_levels = get_previous_session_stats(turso, epic, benchmark_date_str, logger)

                        if df is not None and not df.empty:
                            # 3. RUN THE ENGINE
                            card = analyze_market_context(df, ref_levels, ticker=epic)
                            
                            # Store for AI
                            etf_json_cards.append(json.dumps(card))

                            # Update UI Table
                            mig_count = len(card.get('value_migration_log', []))
                            imp_count = len(card.get('impact_rejections', []))
                            
                            # Calculate freshness for UI bar
                            freshness_score = 0.0
                            try:
                                if price_ts:
                                    ts_clean = str(price_ts).replace("Z", "+00:00").replace(" ", "T")
                                    ts_obj = datetime.fromisoformat(ts_clean)
                                    if ts_obj.tzinfo is None: ts_obj = ts_obj.replace(tzinfo=timezone.utc)
                                    lag_minutes = (simulation_cutoff_dt - ts_obj).total_seconds() / 60.0
                                    freshness_score = max(0.0, 1.0 - (lag_minutes / 60.0))
                            except: pass

                            new_row = {
                                "Ticker": epic,
                                "Price": f"${latest_price:.2f}",
                                "Freshness": freshness_score,
                                "Audit: Date": f"{price_ts} (UTC)",
                                "Migration Blocks": mig_count,
                                "Impact Levels": imp_count,
                            }
                            st.session_state.glassbox_etf_data.append(new_row)
                            etf_placeholder.dataframe(pd.DataFrame(st.session_state.glassbox_etf_data), use_container_width=True)
                            time.sleep(0.02)

                if not etf_json_cards:
                    status.update(label="Scan Aborted: No Data", state="error")
                    if mode == "Simulation":
                        st.error(f"⚠️ No simulation data found for {simulation_cutoff_str} (UTC). Please run Data Harvester for this historical timeframe.")
                    else:
                        st.error("⚠️ No live data found. Please run the Data Harvester to fetch the latest market data.")
                    st.stop()

                status.write("Synthesizing Observation Cards...")
                
                # NEW PROMPT STRUCTURE FOR IMPACT ENGINE
                prompt = f"""
                [SYSTEM]
                You are a Market Auction Theorist. You analyze Market Structure via Time and Impact.
                
                [INPUTS]
                1. EOD Context: {json.dumps(eod_card)}
                2. NEWS: {pm_news}
                3. LIVE AUCTION DATA (JSON OBSERVATION CARDS):
                {etf_json_cards}
                
                [TASK]
                Synthesize the 'State of the Auction'.
                - Identify the Value Migration (Are POCs stepping up/down?).
                - Identify the Impact Zones (Where is the hard rejection?).
                - Output standard Economy Card JSON (marketNarrative, marketBias, sectorRotation).
                """
                
                st.session_state.glassbox_prompt = prompt
                prompt_placeholder.text_area("Prompt Preview", prompt, height=150)

                resp, error_msg = call_gemini_with_rotation(prompt, "You are an Auction Theorist.", logger, selected_model, st.session_state.key_manager_instance)

                if resp:
                    try:
                        clean = re.search(r"(\{.*\})", resp, re.DOTALL).group(1)
                        st.session_state.premarket_economy_card = json.loads(clean)
                        st.session_state.latest_macro_date = st.session_state.analysis_date.isoformat()
                        status.update(label="Macro Card Generated", state="complete")
                        st.rerun()
                    except Exception as e:
                        status.update(label="JSON Parse Error", state="error")
                        st.error(f"AI Error: {e}")
                else:
                    status.update(label="AI Failed", state="error")
                    st.error(error_msg)

        if st.session_state.premarket_economy_card:
            st.success("Macro Card Ready")
            with st.expander("View Final AI Output"):
                st.json(st.session_state.premarket_economy_card)

        render_proximity_scan() 

    # --- TAB 2: HEAD TRADER ---
    with tab2:
        render_battle_commander()

if __name__ == "__main__":
    main()
