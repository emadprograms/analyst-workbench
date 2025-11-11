Analyst Pipeline Engine

Overview

The Analyst Pipeline Engine is a Streamlit web application designed for market analysts to streamline their End-of-Day (EOD) workflow. It combines quantitative data processing with AI-driven qualitative analysis to generate daily "Economy Cards" and "Company Cards."

This application is built on a single source of truth database model, where all historical cards are stored in a unified, date-anchored database. This allows for robust, date-aware processing and gives analysts the ability to go back in time to edit any historical card and re-run the pipeline to fix downstream data.

Features

Date-Aware EOD Pipeline: Run the entire EOD analysis pipeline for any selected date.

AI-Powered Analysis: Uses the Gemini API to generate qualitative insights on market narratives and company-specific price action.

Automated Data Processing: Fetches and processes EOD market data (OHLC, VWAP, Volume Profile, etc.) for stocks and ETFs.

Unified Card Editor: A single, powerful editor. There is no "living" vs. "archived" view. Select any date to view, edit, and save the Economy Card or any Company Card for that day.

Persistent Historical Notes: A dedicated table for analysts to save long-term, static notes on specific tickers (e.g., "Major 5-year support at $150").

Database Viewer: A separate Streamlit page (db_viewer.py) for raw, read-only access to all database tables.

Project Structure

/
├── analysis_database.db       # The new, simplified database file (created by setup_db.py)
├── database/
│   └── analysis_database.db   # (The OLD v1 database file, for migration)
├── pages/
│   ├── eod_workflow.py        # The main application (Tab 1: Pipeline, Tab 2: Editor)
│   └── db_viewer.py           # The database viewer utility
├── modules/
│   ├── ai_services.py         # Handles all Gemini API calls and prompt engineering.
│   ├── config.py              # Stores API keys, DB path, ticker lists, and default JSON.
│   ├── data_processing.py     # Runs quantitative analysis (yfinance) and generates raw summaries.
│   ├── db_utils.py            # Manages all database connections and queries (CRUD operations).
│   └── ui_components.py       # Contains all Streamlit components for rendering cards.
├── setup_db.py                # (SETUP) Script to create the new database schema.
├── migrate_data.py            # (MIGRATION) Script to copy data from the old DB to the new one.
├── inspect_old_db.py          # (UTILITY) A read-only script to check a database's schema.
└── requirements.txt           # (To be created)


Database Schema

This application uses a simplified, single-source-of-truth schema:

daily_inputs: The "anchor" table. The latest date here defines the "Last Processed Date."

date (PRIMARY KEY)

market_news

etf_summaries (replaces combined_etf_summaries)

stocks: Stores only the persistent, manually-edited historical notes.

ticker (PRIMARY KEY)

historical_level_notes

economy_cards: The main table for all economy cards.

date (PRIMARY KEY)

economy_card_json

company_cards: The main table for all company cards.

date (PRIMARY KEY)

ticker (PRIMARY KEY)

raw_text_summary

company_card_json

Setup & Installation

Clone the Repository:

git clone [your-repo-url]
cd analyst-workbench


Create a Virtual Environment (Recommended):

python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate


Install Requirements:
Create a requirements.txt file with the following content:

streamlit
pandas
requests
deepdiff
pytz
yfinance


Then, install them:

pip install -r requirements.txt


Set Up Secrets:
Create a file at .streamlit/secrets.toml and add your Gemini API keys:

[gemini]
api_keys = [
    "AIzaSy...key1",
    "AIzaSy...key2",
    "AIzaSy...key3"
]


Database Setup & Migration

You have two options:

A) First-Time Setup (Clean Slate)

If you are starting fresh and have no old data to migrate:

Run the database setup script from your terminal:

python setup_db.py


A new, empty analysis_database.db file will be created in your root folder.

You can now run the app. Two common options:

- Run the main EOD page directly:

        streamlit run pages/eod_workflow.py

- Or use the single-entry launcher (recommended) which provides a small
    sidebar to pick pages:

        streamlit run app.py

The launcher (`app.py`) will load the available pages from the `pages/`
folder and let you switch between the EOD workflow, the DB viewer, and
other helper pages.

Setting up Python 3.12 (recommended)
-----------------------------------

If your system Python is older (for example Python 3.9), it's best to install
Python 3.12 for this project and recreate the virtual environment. Below are
two supported approaches: the simple Homebrew method (system-level) and the
recommended per-project `pyenv` method.

Quick summary (recommended): use `pyenv` to install Python 3.12 and create a
project-local `.venv`. The commands below are zsh-ready for macOS.

Option A — Homebrew (quick)

1. Install Homebrew (if you don't have it):

```zsh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

2. Install Python 3.12:

```zsh
brew update
brew install python@3.12
```

3. (Optional) Add the Homebrew Python to your PATH in `~/.zshrc`:

```zsh
# Apple Silicon example
echo 'export PATH="/opt/homebrew/opt/python@3.12/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

4. Create the project virtualenv and install deps:

```zsh
cd /path/to/analyst-workbench
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

Option B — pyenv (recommended)

1. Install dependencies and `pyenv`:

```zsh
xcode-select --install
brew update
brew install pyenv openssl readline sqlite3 xz zlib tcl-tk
```

2. Add pyenv init to your shell (`~/.zshrc`):

```zsh
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.zshrc
echo 'export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.zshrc
echo 'eval "$(pyenv init --path)"' >> ~/.zshrc
echo 'eval "$(pyenv init -)"' >> ~/.zshrc
source ~/.zshrc
```

3. Install Python 3.12.x and set it for the project:

```zsh
pyenv install 3.12.2   # or latest 3.12.x from `pyenv install --list`
cd /path/to/analyst-workbench
pyenv local 3.12.2     # writes .python-version to the repo
```

4. Create the venv and install deps:

```zsh
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

Smoke test (verify imports)
---------------------------

After installing dependencies, run the small smoke test to ensure the main
page imports without raising errors (it won't start Streamlit):

```zsh
source .venv/bin/activate
python scripts/smoke_test_import.py
```

If that prints "OK: imported pages.eod_workflow" then imports are fine. If it
raises an error, read the traceback and install any missing OS-level
dependencies (e.g., Tesseract for `pytesseract`) or Python packages.

Automated helper script
-----------------------

There's a helper shell script at `scripts/setup_python_env.sh` that will:
- attempt to use pyenv if available (and print helpful instructions if not),
- create a `.venv`, and
- install `requirements.txt` into it.

Run it with:

```zsh
bash scripts/setup_python_env.sh
```

Note: the script does not attempt to install Homebrew or pyenv for you — it
will guide you if those are missing.

Watchdog (recommended for Streamlit live-reload)
------------------------------------------------

Streamlit uses file watchers to detect code changes. Installing the Python
package `watchdog` enables fast, event-driven notifications on macOS (via
FSEvents) instead of slower polling. The setup script will attempt to ensure
`watchdog` is installed, but on macOS you may need the Xcode Command Line
Tools to build it if a prebuilt wheel isn't available for your Python:

```zsh
# Install Xcode Command Line Tools (if not present)
xcode-select --install

# With your virtualenv active, install watchdog (the helper script does this):
pip install watchdog
```

If `pip install watchdog` fails with compilation errors, ensure Xcode CLT is
installed and that you are using a common Python distribution (Homebrew or
pyenv-installed) so pip can fetch a prebuilt wheel. An alternative system
watcher is Facebook's `watchman` (brew install watchman) but it's not required
for Streamlit — `watchdog` is the typical solution.

B) Migrating from the Old v1 Database

If you have your old data in database/analysis_database.db and want to move it to the new, clean structure:

Run the Setup Script: This creates the new, empty analysis_database.db in your root folder.

python setup_db.py


Confirm Migration Paths: Open the migrate_data.py script and ensure the paths are correct:

OLD_DB_FILE = "database/analysis_database.db" (Your old data)

NEW_DB_FILE = "analysis_database.db" (Your new empty DB)

Run the Migration Script: This will safely copy all data from the old tables to the new ones.

python migrate_data.py


Daily Workflow

Run the Application:

streamlit run pages/eod_workflow.py
