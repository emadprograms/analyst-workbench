# Analyst Workbench

**Analyst Workbench** is a high-performance market intelligence platform designed for senior traders and analysts. It transforms raw market data (price action, volume, VWAP) into sophisticated, date-aware AI narratives. Using a CLI-first architecture with Discord remote control, it automates the generation of "Economy Cards" (Macro) and "Company Cards" (Micro) to maintain a persistent database of market structure and trade plans.

## 🚀 Key Features

*   **Date-Aware EOD Pipeline:** Fully automated workflow to process market news, ETF price action, and individual ticker behavior for any specific date.
*   **Discord-to-GitHub Remote Control:** A custom Discord bot (`!inputnews`, `!updateeconomy`) that dispatches GitHub Actions workflows for serverless execution.
*   **Gemini Key Rotation (KeyManager):** Sophisticated management of Google Gemini (Pro & Flash) API keys, handling rate limits, quotas, and automatic rotation via a central database.
*   **Impact Engine:** Advanced quantitative analysis that calculates "Value Migration" and "Committed Participant" behavior to determine the nature of a trading session.
*   **Infisical Secret Management:** Secure, centralized management of all API keys (GitHub, Discord, Turso, Gemini) via the Infisical platform.
*   **Persistent Market Memory:** Built on **Turso (LibSQL)** to store historical notes, AI-generated cards, and raw news inputs, creating a "Single Source of Truth" for your trading history.

---

## 🛠️ System Architecture

The application is built for reliability and automation, utilizing a modular Python backend and cloud-native execution.

### Core Components

1.  **`main.py`**: The primary CLI entry point. Handles pipeline execution, news input, and database maintenance.
2.  **`discord_bot/bot.py`**: A Discord interaction layer that triggers remote execution via GitHub Actions.
3.  **`modules/`**:
    *   **`ai/`**: Prompt engineering and Gemini API orchestration.
    *   **`analysis/`**: The "Impact Engine" and session analysis logic.
    *   **`core/`**: Configuration, logging, secret management (Infisical), and the KeyManager.
    *   **`data/`**: Database utilities, price data fetching (yfinance), and migrations.
4.  **`.github/workflows/manual_run.yml`**: The automation engine that executes the pipeline on GitHub's infrastructure.

---

## 📦 Setup & Installation

### 1. Environment Setup

Ensure you have **Python 3.12+** installed.

```bash
# Clone the repository
git clone https://github.com/emadprograms/analyst-workbench
cd analyst-workbench

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Secret Management (Infisical)

This project uses **Infisical** for all secrets. You must set the following environment variables in your environment (or GitHub Secrets):

*   `INFISICAL_CLIENT_ID`
*   `INFISICAL_CLIENT_SECRET`
*   `INFISICAL_PROJECT_ID`

These credentials allow the system to securely fetch the Turso DB URL, Discord tokens, and GitHub PATs.

### 3. Database Initialization (Turso)

To create the required tables (`aw_daily_news`, `aw_economy_cards`, `aw_company_cards`, `stocks`, `gemini_api_keys`), run the setup script:

```bash
python modules/data/setup_db.py
```

---

## 🖥️ Usage Guide

### A. CLI Commands (`main.py`)

Run the pipeline or individual tasks directly from your terminal.

*   **Full Pipeline Run:**
    ```bash
    python main.py --action run --date 2026-02-23 --model gemini-3-flash-free
    ```
*   **Update Economy Card Only:**
    ```bash
    python main.py --action update-economy --date 2026-02-23
    ```
*   **Input Market News:**
    ```bash
    python main.py --action input-news --date 2026-02-23 --text "Your news here..."
    ```
*   **Inspect Database:**
    ```bash
    python main.py --action inspect
    ```

### B. Discord Bot Commands

Use these commands in your Discord server to trigger the automation remotely.

*   **`!inputnews [date]`**: Opens a text box to paste headlines or accepts an attached `.txt` file.
*   **`!updateeconomy [date]`**: Dispatches a GitHub Action to generate the day's Macro card.
*   **`!checknews [date]`**: Verifies if news has been successfully ingested for a specific date.
*   **`!inspect`**: Triggers a database health check and reports back via webhook.

---

## 🧩 Directory Structure

```text
analyst-workbench/
├── main.py                # CLI Entry Point
├── requirements.txt       # Dependencies
├── discord_bot/
│   └── bot.py             # Discord Bot Controller
├── modules/
│   ├── ai/                # AI Services & Prompts
│   ├── analysis/          # Impact Engine (Quant Logic)
│   ├── core/              # Config, Keys, Logs, Secrets
│   └── data/              # DB Utils & Data Fetching
├── tests/                 # Pytest Suite
└── .github/workflows/     # Automation (GitHub Actions)
```
