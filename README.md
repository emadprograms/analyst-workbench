# Analyst Workbench

**Analyst Workbench** is a comprehensive market intelligence platform designed to streamline the workflow of financial analysts. It combines quantitative data processing (price action, volume, VWAP) with qualitative AI analysis (narrative generation, sentiment analysis) to create a persistent, date-aware database of market insights.

## 🚀 Features

*   **Date-Aware EOD Workflow:** A robust pipeline to process "Economy Cards" (Macro) and "Company Cards" (Micro) for any specific date.
*   **AI-Powered Analysis:** Utilizes Google Gemini (Pro & Flash) models to generate insights, trade plans, and market narratives.
*   **Gemini Key Rotation System:** A sophisticated `KeyManager` that handles API key rotation, rate limits, and usage quotas automatically.
*   **Unified Editor:** A "Single Source of Truth" editor that allows you to view and modify historical cards seamlessly.
*   **AI Image Parser:** A dedicated tool to extract text from complex images (charts, news screenshots) using Multimodal AI and OCR, saving directly to the database.
*   **Turso (LibSQL) Database:** Built on a remote, scalable database architecture for secure data persistence.

---

## 🛠️ System Architecture

The application is built with **Streamlit** for the frontend and **Turso (LibSQL)** for the backend database.

### The Core Components

1.  **`app.py`**: The landing page and entry point.
2.  **`pages/eod_workflow.py`**: The main engine. Handles data fetching (yfinance), AI analysis, and database CRUD operations.
3.  **`pages/image_parser.py`**: A utility to convert images to text and archive them.
4.  **`modules/key_manager.py`**: The brain of the AI system. It manages a pool of API keys stored in the database to ensure high availability.

---

## 📦 Setup & Installation

### 1. Environment Setup

Ensure you have **Python 3.10+** installed.

```bash
# Clone the repository
git clone [your-repo-url]
cd analyst-workbench

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Database Configuration (Turso)

This project uses **Turso**. You need a Turso database URL and an Auth Token.

Create a file at `.streamlit/secrets.toml`:

```toml
[turso]
db_url = "libsql://your-database-name.turso.io"
auth_token = "your-long-auth-token-here"
```

### 3. API Key Setup (The Key Manager)

Unlike standard apps that store keys in `.env` files, **Analyst Workbench manages AI keys inside the database**. This allows for dynamic rotation and usage tracking.

You must insert your Google Gemini API keys into the `gemini_api_keys` table in your Turso database.

**Table Schema (`gemini_api_keys`):**
*   `key_name` (TEXT): A nickname for the key (e.g., "Pro_Key_1").
*   `key_value` (TEXT): The actual API key starting with `AIza...`.
*   `priority` (INT): Order of use (Lower = Higher priority).

**How to Add Keys:**
You can use the Turso CLI or the Turso dashboard web UI to run this SQL command:

```sql
INSERT INTO gemini_api_keys (key_name, key_value, priority)
VALUES
('My_Primary_Key', 'AIzaSyYourKeyHere...', 10),
('My_Backup_Key', 'AIzaSyYourBackupKey...', 20);
```

*The system will automatically detect these keys and start rotating them.*

---

## 🗄️ Database Initialization

To create the required tables (`daily_inputs`, `economy_cards`, `company_cards`, `data_archive`), run the setup script.

**⚠️ WARNING: This script wipes existing data tables (except `stocks`). Use with caution on a production DB.**

You must export your credentials as environment variables for this script to work (since it doesn't read Streamlit secrets):

**Linux/Mac:**
```bash
export TURSO_DB_URL="libsql://your-db.turso.io"
export TURSO_AUTH_TOKEN="your-token"
python modules/setup_db.py
```

**Windows (PowerShell):**
```powershell
$env:TURSO_DB_URL="libsql://your-db.turso.io"
$env:TURSO_AUTH_TOKEN="your-token"
python modules/setup_db.py
```

---

## 🖥️ Usage Guide

Run the application:

```bash
streamlit run app.py
```

### 1. Pipeline Runner (EOD Workflow)
Navigate to the **EOD Workflow** page.
1.  **Step 1:** Select a Date and input the "Market News Summary" (Manual context). Save it.
2.  **Step 2:** Click "Generate Economy Card". The AI will analyze ETF data + your news to create the Macro card.
3.  **Step 3:** Select Tickers and click "Run Update". The AI analyzes price action/volume for each stock and generates Company Cards.

### 2. Card Editor
Use the **Card Editor** tab in the EOD Workflow page to go back in time. You can view or edit the JSON structure of any card for any date. This is the "Single Source of Truth."

### 3. Image Parser
Navigate to the **Image Parser** page.
*   Upload screenshots of news, charts, or documents.
*   Select "AI Extraction" or "Tesseract OCR".
*   Click **Save to Database Archive** to store the text permanently in the `data_archive` table.

---

## 🧩 Directory Structure

```
/
├── app.py                 # Landing page
├── modules/
│   ├── ai_services.py     # AI Logic (Prompt Engineering)
│   ├── config.py          # App Configuration
│   ├── data_processing.py # Quantitative Data (yfinance)
│   ├── db_utils.py        # Database Interactions
│   ├── key_manager.py     # AI Key Rotation System
│   ├── setup_db.py        # Database Schema Setup Script
│   └── ui_components.py   # Streamlit UI Helpers
├── pages/
│   ├── eod_workflow.py    # Main Application Logic
│   └── image_parser.py    # Image Intelligence Tool
├── requirements.txt       # Python Dependencies
└── README.md              # Documentation
```
