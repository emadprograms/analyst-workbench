import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_PAT")
GITHUB_REPO = os.getenv("GITHUB_REPO", "emadprograms/analyst-workbench") 
WORKFLOW_FILENAME = "manual_run.yml"
ACTIONS_URL = f"<https://github.com/{GITHUB_REPO}/actions>"

# --- Ticker Configuration ---
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
