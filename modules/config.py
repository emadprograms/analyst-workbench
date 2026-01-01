import streamlit as st
import logging
import os

# --- Local Import ---
# import removed to break cycle 

# ==========================================
# 1. API CONFIGURATION
# ==========================================

# Define available models for the UI (Used in Dropdowns)
AVAILABLE_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.5-flash", 
    "gemini-2.5-flash-lite",
    "gemma-3-27b-it",
    "gemma-3-12b-it",
    "gemini-2.5-pro", # Paid Tier Only
    "gemini-3-pro-preview",  # Paid Tier Only - Corrected from 3.0
    "gemini-3-flash-preview" # Added request
]

# Default Model (Fallback)
MODEL_NAME = "gemini-2.5-flash-lite" 

# --- FIX IS HERE: Define the Base URL without the model name ---
API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Construct the legacy URL for backward compatibility (optional but safe)
API_URL = f"{API_BASE_URL}/{MODEL_NAME}:generateContent"


# ==========================================
# 2. KEY MANAGER (The "Brain")
# ==========================================
KEY_MANAGER = None
TURSO_DB_URL = None
TURSO_AUTH_TOKEN = None

try:
    # Load Turso Secrets
    turso_secrets = st.secrets.get("turso", {})
    TURSO_DB_URL = turso_secrets.get("db_url")
    TURSO_AUTH_TOKEN = turso_secrets.get("auth_token")
    
    if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
        logging.critical("CRITICAL: Turso DB URL or Auth Token not found in st.secrets.")

except Exception as e:
    logging.critical(f"Error loading secrets: {e}")


# ==========================================
# 3. API KEYS (Legacy/Fallback Support)
# ==========================================
try:
    gemini_secrets = st.secrets.get("gemini", {})
    API_KEYS = gemini_secrets.get("api_keys", []) 
except Exception:
    API_KEYS = []


# ==========================================
# 4. TICKER LISTS
# ==========================================
STOCK_TICKERS = [
    "AAPL", "AMZN", "APP", "ABT", "PEP", "TSLA", "NVDA", "AMD",
    "SNOW", "NET", "PLTR", "MU", "ORCL", "TSM",
    "ADBE", "AVGO", "BABA", "GOOGL", "LRCX", "META", "MSFT", 
    "NDAQ", "PANW", "QCOM", "SHOP"
]
ETF_TICKERS = [
    "SPY", "QQQ", "IWM", "DIA", "TLT", "XLK", "XLF", "XLP", "XLE",
    "SMH", "XLI", "XLV", "UUP", "PAXGUSDT", "BTCUSDT",
    "XLC", "XLU", "EURUSDT", "CL=F", "^VIX"
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