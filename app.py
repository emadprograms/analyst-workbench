import streamlit as st
from datetime import datetime

st.set_page_config(
    page_title="Analyst Workbench",
    page_icon="📊",
    layout="wide"
)

st.title("📊 Analyst Workbench")
st.subheader("AI-Powered Market Intelligence System")

st.markdown(
    """
    Welcome to the **Analyst Workbench**, a unified platform for combining quantitative market data
    with qualitative AI-driven analysis.

    This system is designed to streamline the End-of-Day (EOD) workflow, manage market narratives,
    and maintain a persistent, date-aware database of "Economy Cards" and "Company Cards."
    """
)

st.divider()

col1, col2 = st.columns(2)

with col1:
    st.info("#### 🚀 EOD Workflow Engine")
    st.markdown(
        """
        **Core Functionality:**
        *   **Daily Inputs:** Capture manual market summaries and news.
        *   **Economy Card:** Auto-generate global macro cards using ETF data + AI.
        *   **Company Cards:** Process specific tickers (OHLC, VWAP, Vol) + AI analysis.
        *   **Editor:** Review and modify any historical card.

        👉 **Select `eod_workflow` in the sidebar to begin.**
        """
    )

with col2:
    st.info("#### 🖼️ AI Image Intelligence")
    st.markdown(
        """
        **Core Functionality:**
        *   **OCR + Vision:** Upload scrolling screenshots or charts.
        *   **AI Extraction:** Converts complex visual data into clean text.
        *   **Archive:** Save extracted intel (News, Briefings) directly to the database.

        👉 **Select `image_parser` in the sidebar to begin.**
        """
    )

st.divider()

with st.expander("ℹ️ System Architecture & Status"):
    st.write("**Database:** Turso (LibSQL) - Remote, Secure, Scalable.")
    st.write("**AI Model:** Gemini Pro/Flash (via Rotation Manager).")
    st.write(f"**Current Server Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
