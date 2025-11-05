import streamlit as st
import textwrap
import json

# --- Logger Class ---
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

def escape_markdown(text):
    """Escapes special Markdown characters in a string for safe rendering."""
    if not isinstance(text, str):
        return text
    return text.replace('$', '\\$').replace('~', '\\~')

# --- THIS FUNCTION IS MODIFIED ---
def display_view_market_note_card(card_data, show_edit_button: bool = True):
    """Displays the data in a read-only, formatted Markdown view."""
    data = card_data
    with st.container(border=True):
        title_col, button_col = st.columns([0.95, 0.05])
        with title_col:
            st.header(escape_markdown(data.get('marketNote', '')))
        with button_col:
            st.write("") 
            # --- THIS BLOCK IS NOW CONDITIONAL ---
            if show_edit_button:
                if st.button("✏️", help="Edit card"):
                    st.session_state.edit_mode = True
                    st.rerun()
            # -------------------------------------

        if "basicContext" in data:
            st.subheader(escape_markdown(data["basicContext"].get('tickerDate', '')))
        st.markdown(f"**Confidence:** {escape_markdown(data.get('confidence', 'N/A'))}")
        with st.expander("Show Screener Briefing"):
            st.info(escape_markdown(data.get('screener_briefing', 'N/A')))
        st.divider()

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
                    - **Key Action:** {escape_markdown(tech.get('keyAction', 'N/A'))}
                """))
        st.divider()

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

def display_editable_market_note_card(card_data):
    """Displays the raw JSON for the market note card for editing."""
    st.info("You are in raw JSON edit mode. Ensure the final text is valid JSON before saving.")
    
    try:
        # Pretty-print the JSON for readability
        json_string = json.dumps(card_data, indent=2)
    except Exception as e:
        st.error(f"Could not serialize card data to JSON: {e}")
        json_string = "{}"

    # The text area will hold the JSON string.
    edited_json_string = st.text_area(
        "Company Overview Card JSON",
        value=json_string,
        height=600,
        key="editable_market_note_json"
    )

    # The function now returns the raw string for the main app to validate and save.
    return edited_json_string

# --- THIS FUNCTION IS MODIFIED ---
def display_view_economy_card(card_data, key_prefix="eco_view", show_edit_button: bool = True):
    """Displays the Economy card data in a read-only, formatted Markdown view."""
    data = card_data
    with st.expander("Global Economy Card", expanded=True):
        with st.container(border=True):
            title_col, button_col = st.columns([0.95, 0.05])
            with title_col:
                st.markdown(f"**{escape_markdown(data.get('marketNarrative', 'Market Narrative N/A'))}**")
            with button_col:
                st.write("")
                # --- THIS BLOCK IS NOW CONDITIONAL ---
                if show_edit_button:
                    if st.button("✏️", key=f"{key_prefix}_edit_button", help="Edit economy card"):
                        st.session_state.edit_mode_economy = True
                        st.rerun()
                # -------------------------------------

            st.markdown(f"**Market Bias:** {escape_markdown(data.get('marketBias', 'N/A'))}")
            st.markdown("---")
            col1, col2 = st.columns(2)

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
                    for index, analysis in indices.items():
                        if analysis and analysis.strip():
                            st.markdown(f"**{index.replace('_', ' ')}**")
                            st.write(escape_markdown(analysis))

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
            st.markdown("##### Market Key Action")
            st.text(escape_markdown(data.get('marketKeyAction', 'N/A')))

def display_editable_economy_card(card_data, key_prefix="eco_edit"):
    """Displays the raw JSON for the economy card for editing."""
    st.info("You are in raw JSON edit mode. Ensure the final text is valid JSON before saving.")
    
    try:
        # Pretty-print the JSON for readability
        json_string = json.dumps(card_data, indent=2)
    except Exception as e:
        st.error(f"Could not serialize card data to JSON: {e}")
        json_string = "{}"

    # The text area will hold the JSON string.
    edited_json_string = st.text_area(
        "Economy Card JSON",
        value=json_string,
        height=600,
        key=f"{key_prefix}_editable_economy_json"
    )
    
    # The function now returns the raw string for the main app to validate and save.
    return edited_json_string