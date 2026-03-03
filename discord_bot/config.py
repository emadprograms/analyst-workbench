import os
import logging
from dotenv import load_dotenv
from modules.core.infisical_manager import InfisicalManager

load_dotenv()

# Initialize Infisical Manager
infisical_mgr = InfisicalManager()

# --- Secrets Retrieval (Infisical first, then Env) ---
DISCORD_TOKEN = infisical_mgr.get_secret("DISCORD_BOT_TOKEN")
if not DISCORD_TOKEN:
    DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

GITHUB_TOKEN = infisical_mgr.get_secret("GITHUB_PAT")
if not GITHUB_TOKEN:
    GITHUB_TOKEN = os.getenv("GITHUB_PAT")

GITHUB_REPO = os.getenv("GITHUB_REPO", "emadprograms/analyst-workbench") 
WORKFLOW_FILENAME = "manual_run.yml"
ACTIONS_URL = f"https://github.com/{GITHUB_REPO}/actions"

if not DISCORD_TOKEN:
    logging.error("⚠️ DISCORD_BOT_TOKEN not found in Infisical or Environment.")
if not GITHUB_TOKEN:
    logging.error("⚠️ GITHUB_PAT not found in Infisical or Environment.")

# --- Ticker Configuration ---
STOCK_TICKERS = [
    "AAPL", "AMZN", "APP", "ABT", "PEP", "TSLA", "NVDA", "AMD",
    "SNOW", "NET", "PLTR", "MU", "ORCL", "TSM",
    "ADBE", "AVGO", "BABA", "GOOGL", "META", "MSFT", 
    "NDAQ", "PANW", "QCOM", "SHOP"
]
ETF_TICKERS = [
    "SPY", "QQQ", "IWM", "DIA", "TLT", "XLK", "XLF", "XLP", "XLE",
    "SMH", "XLI", "XLV", "UUP", "PAXGUSDT", "BTCUSDT",
    "XLC", "XLU", "EURUSDT", "CL=F", "^VIX"
]
ALL_TICKERS = sorted(STOCK_TICKERS + ETF_TICKERS)
