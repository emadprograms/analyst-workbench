"""
This module provides functions to display market note data in a Streamlit application.
It offers two main display modes: a read-only view and an editable view.

- `display_view_market_note_card`: Renders market data in a static, formatted card
  for viewing purposes. It includes sections for fundamental context, behavioral sentiment,
  technical structure, and trade plans.

- `display_editable_market_note_card`: Renders the market data as raw JSON in a
  text area for direct editing.

A helper function `escape_markdown` is also included to safely render text within
Markdown components by escaping special characters.
"""

import streamlit as st
import textwrap
import json

# --- Logger Class (as imported by your main app) ---
class AppLogger:
    def __init__(self, st_container=None):
        self.st_container = st_container
    def log(self, message):
        safe_message = str(message).replace('<', '&lt;').replace('>', '&gt;')
        if self.st_container: self.st_container.markdown(safe_message, unsafe_allow_html=True)
        else: print(message)
    def log_code(self, data, language='json'):
        try:
            if isinstance(data, dict): formatted_data = json.dumps(data, indent=2, ensure_ascii=False)
            elif isinstance(data, str):
                try: formatted_data = json.dumps(json.loads(data), indent=2, ensure_ascii=False)
                except: formatted_data = data
            else: formatted_data = str(data)
            escaped_data = formatted_data.replace('`', '\\`')
            log_message = f"```{language}\n{escaped_data}\n```"
            if self.st_container: self.st_container.markdown(log_message, unsafe_allow_html=False)
            else: print(log_message)
        except Exception as e: self.log(f"Err format log: {e}"); self.log(str(data))


# --- Helper Function ---
def escape_markdown(text):
    """Escapes special Markdown characters in a string for safe rendering."""
    if not isinstance(text, str):
        return text
    # Escape $ and ~
    return text.replace('$', '\\$').replace('~', '\\~')

# --- COMPANY CARD (VIEW) ---
def display_view_market_note_card(card_data, edit_mode_key="edit_mode"):
    """
    Displays the data in a read-only, formatted Markdown view.
    Accepts an 'edit_mode_key' to toggle the correct session state variable.
    """
    data = card_data
    with st.container(border=True):
        # Header with Edit button on the right
        title_col, button_col = st.columns([0.95, 0.05])

        with title_col:
            st.header(escape_markdown(data.get('marketNote', '')))
        
        with button_col:
            st.write("") # Add vertical space to align button

            # Create a unique key for the button itself
            button_key = f"edit_btn_{data.get('basicContext', {}).get('tickerDate', 'default_key')}_{edit_mode_key}"

            # Use the new edit_mode_key to set the correct session state.
            # Use an on_click callback that sets session state and triggers a rerun
            # so a single click immediately switches to edit mode.
            def _enter_edit_mode():
                st.session_state[edit_mode_key] = True
                try:
                    st.experimental_rerun()
                except Exception:
                    # If rerun is unavailable in the current environment, ignore the error.
                    pass

            # Attach the callback to the button; we don't need to check its return value.
            st.button("✏️", help="Edit card", key=button_key, on_click=_enter_edit_mode)
        
        if "basicContext" in data:
            st.subheader(escape_markdown(data["basicContext"].get('tickerDate', '')))
        st.markdown(f"**Confidence:** {escape_markdown(data.get('confidence', 'N/A'))}")
        with st.expander("Show Screener Briefing", expanded=True):
            st.info(escape_markdown(data.get('screener_briefing', 'N/A')))
        st.divider()

        # Columns
        col1, col2 = st.columns(2)
        with col1:
            with st.container(border=True):
                st.markdown("##### Fundamental Context")
                fund = data.get("fundamentalContext", {})
                st.markdown(textwrap.dedent(f"""
                    - **Valuation:** {escape_markdown(fund.get('valuation', 'N/A'))}
                    - **Analyst Sentiment:** {escape_markdown(fund.get('analystSentiment', 'N/A'))}
                    - **Insider Activity:** {escape_markdown(fund.get('insiderActivity', 'N/A'))}
                    - **Peer Performance:** {escape_markdown(fund.get('peerPerformance', 'N/A'))}
                """))
            with st.container(border=True):
                st.markdown("##### Behavioral & Sentiment")
                sent = data.get("behavioralSentiment", {})
                st.markdown(textwrap.dedent(f"""
                    - **Buyer vs. Seller:** {escape_markdown(sent.get('buyerVsSeller', 'N/A'))}
                    - **Emotional Tone:** {escape_markdown(sent.get('emotionalTone', 'N/A'))}
                    - **News Reaction:** {escape_markdown(sent.get('newsReaction', 'N/A'))}
                """))
        with col2:
            with st.container(border=True):
                st.markdown("##### Basic Context")
                ctx = data.get("basicContext", {})
                st.markdown(textwrap.dedent(f"""
                    - **Company:** {escape_markdown(ctx.get('companyDescription', 'N/A'))}
                    - **Sector:** {escape_markdown(ctx.get('sector', 'N/A'))}
                    - **Recent Catalyst:** {escape_markdown(ctx.get('recentCatalyst', 'N/A'))}
                """))
            with st.container(border=True):
                st.markdown("##### Technical Structure")
                tech = data.get("technicalStructure", {})
                st.markdown(textwrap.dedent(f"""
                    - **Major Support:** {escape_markdown(tech.get('majorSupport', 'N/A'))}
                    - **Major Resistance:** {escape_markdown(tech.get('majorResistance', 'N/A'))}
                """))
                
                # --- REFACTORED: Display the new 'pattern' and 'keyActionLog' ---
                st.markdown(f"**Pattern:** {escape_markdown(tech.get('pattern', 'N/A'))}")
                # --- NEW: Display volumeMomentum so volume analysis is visible in the card ---
                st.markdown(f"**Volume Momentum:** {escape_markdown(tech.get('volumeMomentum', 'N/A'))}")
                
                key_log = tech.get('keyActionLog', [])
                if isinstance(key_log, list) and key_log:
                    with st.expander("Show Full Key Action Log..."):
                        for entry in reversed(key_log): # Show most recent first
                            if isinstance(entry, dict):
                                st.markdown(f"**{entry.get('date', 'N/A')}:** {escape_markdown(entry.get('action', 'N/A'))}")
                            else:
                                st.text(escape_markdown(entry)) # Fallback for old data
                
                # --- Fallback for old data model ---
                elif 'keyAction' in tech:
                    st.markdown(f"**Key Action (Old):**")
                    with st.expander("Show Full Key Action Log..."):
                        st.text(escape_markdown(tech.get('keyAction', 'N/A')))
                # --- END REFACTOR ---

        st.divider()

        # Trade Plans
        st.subheader("Trade Plans")
        def render_plan(plan_data):
            st.markdown(f"#### {escape_markdown(plan_data.get('planName', 'N/A'))}")
            if "scenario" in plan_data and plan_data['scenario']:
                st.info(f"**Scenario:** {escape_markdown(plan_data['scenario'])}")
            st.markdown(textwrap.dedent(f"""
                - **Known Participants:** {escape_markdown(plan_data.get('knownParticipant', 'N/A'))}
                - **Expected Participants:** {escape_markdown(plan_data.get('expectedParticipant', 'N/A'))}
            """))
            st.success(f"**Trigger:** {escape_markdown(plan_data.get('trigger', 'N/A'))}")
            st.error(f"**Invalidation:** {escape_markdown(plan_data.get('invalidation', 'N/A'))}")

        primary_plan_tab, alternative_plan_tab = st.tabs(["Primary Plan", "Alternative Plan"])
        with primary_plan_tab:
            if "openingTradePlan" in data:
                render_plan(data["openingTradePlan"])
        with alternative_plan_tab:
            if "alternativePlan" in data:
                render_plan(data["alternativePlan"])

# --- COMPANY CARD (EDIT) ---
def display_editable_market_note_card(card_data):
    """
    Displays the data in an editable layout with input widgets.
    Returns the edited data as a JSON string.
    """
    try:
        # Pretty-print the JSON for editing
        json_string = json.dumps(card_data, indent=4)
    except Exception as e:
        st.error(f"Failed to serialize card data for editing: {e}")
        json_string = "{}" # Fallback

    st.warning("You are in raw edit mode. Be careful to maintain valid JSON structure.")
    
    # Display in a tall text area
    edited_json_string = st.text_area(
        "Edit Raw JSON",
        value=json_string,
        height=600,
        label_visibility="collapsed"
    )
    
    # Return the (potentially) modified string
    return edited_json_string

# --- ECONOMY CARD (VIEW) ---
def display_view_economy_card(card_data, key_prefix="eco_view", edit_mode_key="edit_mode_economy"):
    """
    Displays the Economy card data in a read-only, formatted Markdown view.
    Accepts an 'edit_mode_key' to toggle the correct session state variable.
    """
    data = card_data
    with st.expander("Global Economy Card", expanded=True):
        with st.container(border=True):
            
            title_col, button_col = st.columns([0.95, 0.05])
                
            with title_col:
                st.markdown(f"**{escape_markdown(data.get('marketNarrative', 'Market Narrative N/A'))}**")
            
            with button_col:
                st.write("")
                # Use the new edit_mode_key to set the correct session state
                def _enter_econ_edit_mode():
                    st.session_state[edit_mode_key] = True
                    try:
                        st.rerun()
                    except Exception:
                        pass

                st.button("✏️", key=f"{key_prefix}_edit_button", help="Edit economy card", on_click=_enter_econ_edit_mode)

            st.markdown(f"**Market Bias:** {escape_markdown(data.get('marketBias', 'N/A'))}")
            st.markdown("---")
            col1, col2 = st.columns(2)

            # Column 1: Key Economic Events and Index Analysis
            with col1:
                with st.container(border=True):
                    st.markdown("##### Key Economic Events")
                    events = data.get("keyEconomicEvents", {})
                    st.markdown("**Last 24h:**")
                    st.info(escape_markdown(events.get('last_24h', 'N/A')))
                    st.markdown("**Next 24h:**")
                    st.warning(escape_markdown(events.get('next_24h', 'N/A')))

                with st.container(border=True):
                    st.markdown("##### Index Analysis")
                    indices = data.get("indexAnalysis", {})
                    # --- REFACTORED: Display the new 'pattern' ---
                    st.markdown(f"**Pattern:** {escape_markdown(indices.get('pattern', 'N/A'))}")
                    for index, analysis in indices.items():
                        if index != 'pattern' and analysis and analysis.strip(): # Don't print pattern twice
                            st.markdown(f"**{index.replace('_', ' ')}**")
                            st.write(escape_markdown(analysis))
                    # --- END REFACTOR ---

            # Column 2: Sector Rotation and Inter-Market Analysis
            with col2:
                with st.container(border=True):
                    st.markdown("##### Sector Rotation")
                    rotation = data.get("sectorRotation", {})
                    st.markdown(f"**Leading:** {escape_markdown(', '.join(rotation.get('leadingSectors', [])) or 'N/A')}")
                    st.markdown(f"**Lagging:** {escape_markdown(', '.join(rotation.get('laggingSectors', [])) or 'N/A')}")
                    st.markdown("**Analysis:**")
                    st.write(escape_markdown(rotation.get('rotationAnalysis', 'N/A')))

                with st.container(border=True):
                    st.markdown("##### Inter-Market Analysis")
                    intermarket = data.get("interMarketAnalysis", {})
                    for asset, analysis in intermarket.items():
                        if analysis and analysis.strip():
                            st.markdown(f"**{asset.replace('_', ' ')}**")
                            st.write(escape_markdown(analysis))

            st.markdown("---")
            
            # --- REFACTORED: Display the new 'keyActionLog' ---
            st.markdown("##### Market Key Action Log")
            key_log = data.get('keyActionLog', [])
            if isinstance(key_log, list) and key_log:
                with st.expander("Show Full Market Action Log..."):
                    for entry in reversed(key_log): # Show most recent first
                        if isinstance(entry, dict):
                            st.markdown(f"**{entry.get('date', 'N/A')}:** {escape_markdown(entry.get('action', 'N/A'))}")
                        else:
                            st.text(escape_markdown(entry)) # Fallback for old data
            elif 'marketKeyAction' in data:
                 # Fallback for old data models
                 st.text(escape_markdown(data.get('marketKeyAction', 'N/A')))
            # --- END REFACTOR ---


# --- ECONOMY CARD (EDIT) ---
def display_editable_economy_card(card_data, key_prefix="eco_edit"):
    """
    Displays the Economy card data in an editable layout.
    Returns the edited data as a JSON string.
    """
    try:
        # Pretty-print the JSON for editing
        json_string = json.dumps(card_data, indent=4)
    except Exception as e:
        st.error(f"Failed to serialize card data for editing: {e}")
        json_string = "{}" # Fallback

    st.warning("You are in raw edit mode. Be careful to maintain valid JSON structure.")
    
    # Display in a tall text area
    edited_json_string = st.text_area(
        "Edit Raw JSON",
        value=json_string,
        height=600,
        label_visibility="collapsed",
        key=f"{key_prefix}_json_edit" # Add a key for uniqueness
    )
    
    # Return the (potentially) modified string
    return edited_json_string