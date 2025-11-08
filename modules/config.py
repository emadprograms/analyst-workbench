import os
from datetime import date
import streamlit as st 

# --- API Configuration ---
MODEL_NAME = "gemini-2.5-pro" 
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent"

# --- Load Gemini API Keys ---
try:
    gemini_secrets = st.secrets.get("gemini", {})
    API_KEYS = gemini_secrets.get("api_keys", [])
    if not API_KEYS or not isinstance(API_KEYS, list) or len(API_KEYS) == 0:
        st.error("Error: Gemini API keys not found in st.secrets.")
        st.info("Please add [gemini] section with api_keys = [...] to your .streamlit/secrets.toml file.")
        st.stop()
except Exception as e:
    # Fallback for when st.secrets isn't available (e.g., testing)
    print(f"Warning: Could not load st.secrets (e.g., in a test). {e}")
    API_KEYS = []

# --- (NEW) Load Turso Database Configuration ---
try:
    turso_secrets = st.secrets.get("turso", {})
    TURSO_DB_URL = turso_secrets.get("db_url")
    TURSO_AUTH_TOKEN = turso_secrets.get("auth_token")
    
    if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
        st.error("Error: Turso DB URL or Auth Token not found in st.secrets.")
        st.info("Please add [turso] section to your .streamlit/secrets.toml file.")
        st.stop()
except Exception as e:
    st.error(f"Failed to load Turso secrets. App cannot connect to DB. Error: {e}")
    print(f"Warning: Could not load st.secrets (e.g., in a test). {e}")
    TURSO_DB_URL = None
    TURSO_AUTH_TOKEN = None


# --- Ticker Grouping ---
STOCK_TICKERS = [
    "AAPL", "AMZN", "APP", "ABT", "PEP", "TSLA", "NVDA", "AMD",
    "SNOW", "NET", "PLTR", "MU", "ORCL", "TSM"
]
ETF_TICKERS = [
  "SPY", "QQQ", "IWM", "DIA", "TLT", "XLK", "XLF", "XLP", "XLE",
  "SMH", "XLI", "XLV", "UUP", "GLD",
  "BTC-USD"
]
ALL_TICKERS = sorted(STOCK_TICKERS + ETF_TICKERS)


# --- Default JSON Structures ---

# --- REFACTORED: This now uses the new 'pattern' and 'keyActionLog' structure ---
DEFAULT_COMPANY_OVERVIEW_JSON = """
{
  "marketNote": "Executor's Battle Card: TICKER",
  "confidence": "Medium - Awaiting confirmation",
  "screener_briefing": "AI Updates: High-level bias for screener. Ignore for trade decisions.",
  "basicContext": {
    "tickerDate": "TICKER | YYYY-MM-DD",
    "sector": "Set in Static Editor / Preserved",
    "companyDescription": "Set in Static Editor / Preserved",
    "priceTrend": "AI Updates: Cumulative trend relative to major levels",
    "recentCatalyst": "Set in Static Editor, AI may update if action confirms"
  },
  "technicalStructure": {
    "majorSupport": "AI RULE: READ-ONLY. Update only if decisively broken & confirmed over multiple days.",
    "majorResistance": "AI RULE: READ-ONLY. Update only if decisively broken & confirmed over multiple days.",
    "pattern": "AI RULE: AI will provide a new, high-level summary of the current pattern here.",
    "keyActionLog": [],
    "volumeMomentum": "AI Updates: Volume qualifier for action AT key levels."
  },
  "fundamentalContext": {
    "valuation": "AI RULE: READ-ONLY (Set during initialization/manual edit)",
    "analystSentiment": "AI RULE: READ-ONLY (Set during initialization/manual edit)",
    "insiderActivity": "AI RULE: READ-ONLY (Set during initialization/manual edit)",
    "peerPerformance": "AI Updates: How stock performed relative to peers today."
  },
  "behavioralSentiment": {
    "buyerVsSeller": "AI Updates: Who won the battle at MAJOR levels today?",
    "emotionalTone": "AI Updates: Current market emotion for this stock.",
    "newsReaction": "AI Updates: How did price react to news relative to levels?"
  },
  "openingTradePlan": {
    "planName": "AI Updates: Primary plan (e.g., 'Long from Major Support')",
    "knownParticipant": "AI Updates: Who is confirmed at the level?",
    "expectedParticipant": "AI Updates: Who acts if trigger hits?",
    "trigger": "AI Updates: Specific price action validating this plan.",
    "invalidation": "AI Updates: Price action proving this plan WRONG."
  },
  "alternativePlan": {
    "planName": "AI Updates: Competing plan (e.g., 'Failure at Major Resistance')",
    "scenario": "AI Updates: When does this plan become active?",
    "knownParticipant": "AI Updates: Who is confirmed if scenario occurs?",
    "expectedParticipant": "AI Updates: Who acts if trigger hits?",
    "trigger": "AI Updates: Specific price action validating this plan.",
    "invalidation": "AI Updates: Price action proving this plan WRONG."
  }
}
"""
# --- END REFACTOR ---

# --- REFACTORED: This now uses the new 'pattern' and 'keyActionLog' structure ---
DEFAULT_ECONOMY_CARD_JSON = """
{
  "marketNarrative": "AI Updates: The current dominant story driving the market.",
  "marketBias": "Neutral",
  "keyActionLog": [],
  "keyEconomicEvents": {
    "last_24h": "AI Updates: Summary of recent major data releases and their impact.",
    "next_24h": "AI Updates: List of upcoming high-impact events."
  },
  "sectorRotation": {
    "leadingSectors": [],
    "laggingSectors": [],
    "rotationAnalysis": "AI Updates: Analysis of which sectors are showing strength/weakness."
  },
  "indexAnalysis": {
    "pattern": "AI RULE: AI will provide a new, high-level summary of the current market pattern here.",
    "SPY": "AI Updates: Summary of SPY's current position relative to its own major levels.",
    "QQQ": "AI Updates: Summary of QQQ's current position relative to its own major levels."
  },
  "interMarketAnalysis": {
    "bonds": "AI Updates: Analysis of bond market (e.g., TLT performance, yield movements) and its implication for equities.",
    "commodities": "AI Updates: Analysis of key commodities (e.g., Gold/GLD, Oil/USO) for inflation/safety signals.",
    "currencies": "AI Updates: Analysis of the US Dollar (e.g., UUP/DXY) and its impact on risk assets.",
    "crypto": "AI Updates: Analysis of Crypto (e.g., BTC) as a speculative risk gauge."
  },
  "marketInternals": {
    "volatility": "AI Updates: VIX analysis (e.g., 'VIX is falling, suggesting decreasing fear.')."
  }
}
"""
# --- END REFACTOR ---