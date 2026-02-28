# Analyst Workbench: AI Instructions & System Architecture

This document serves as the "System Knowledge Base" for the AI Agent (Antigravity) and human developers. It defines the core philosophy, infrastructure, and analytical rules engine.

---

## 1. System Architecture

The **Analyst Workbench** is a Streamlit-based Python application designed to act as an "AI Trading Assistant." It ingests raw market data, processes it via custom algorithms, and uses an LLM (Gemini) to generate actionable "Cards" for the user.

### Key Components

*   **Database (Turso/SQLite)**:
    *   `market_data`: Stores raw OHLCV price bars. (Sources: Yahoo Finance)
    *   `aw_company_cards`: Stores the JSON output of the AI analysis (The "living memory" of the stock).
    *   `aw_economy_cards`: Stores the JSON output of the Global Macro analysis.
    *   `aw_daily_inputs`: Stores the daily raw news/macro context provided by the user.
    *   `aw_ticker_notes`: Stores per-ticker historical level notes (user-managed).
    *   `aw_stocks`: The active stock watch list.

*   **Computation Layer (Python)**:
    *   `modules/analysis/impact_engine.py`: The quantitative heart. Slices price action into 3 sessions (Pre, RTH, Post), detects "Impact Levels" (Support/Resistance), tracks "Value Migration" (30min blocks), and calculates **Volume Profiles** (POC, VAH, VAL) and Key Volume Events. Also provides `get_latest_price_details` for market-data validation.
    *   `modules/ai/ai_services.py`: The logic layer. Constructs the massive "Masterclass" prompts, manages API keys (`KeyManager`), and parses the AI's JSON response.
    *   `main.py`: The CLI entry point. Handles argument parsing, pipeline orchestration (`run_update_economy`, `run_update_company`, `run_pipeline`), and Discord webhook reporting.
    *   **Discord Bot (`discord_bot/bot.py`)**: The Command & Control layer.
        *   **Orchestration**: Dispatches heavy compute tasks (Card Building) to GitHub Actions to maintain a serverless architecture and keep Railway costs near zero.
        *   **Local Ingestion**: Directly handles `!inputnews` to save news context to the database without GitHub Actions. Supports manual text entry, file attachments (.txt, .log), and URL fetching (with auto-conversion of Pastebin links to raw format).
        *   **Direct Interaction**: Performs lightweight, low-compute tasks (Retrieving Cards, Editing Historical Notes, Checking News Ingestion, DB Inspection) directly against the database for instantaneous user feedback.
        *   **Dynamic Discovery**: Fetches the active stock watch list directly from `aw_ticker_notes`, eliminating hardcoded lists in the UI.

*   **Caching Layer (Context Freezing)**:
    *   **Goal**: Reduce DB reads.
    *   **Mechanism**: The `impact_engine` checks for a local file `cache/context/{ticker}_{date}.json`. If found **and valid**, it loads it instantly. Use `impact_engine.get_or_compute_context`.
    *   **Validation Gate (`_is_valid_context`)**: A cached file is only served if it passes the validity check: `status != "No Data"` AND `meta.data_points > 0`. Files that fail this check are deleted from disk and re-computed. This prevents stale "No Data" results from being returned forever when the DB was transiently empty.
    *   **Write Gate**: Only a result that passes `_is_valid_context` is written to disk. A failed computation never enters the cache.
    *   **Numpy Serialisation**: The `json.dump` call uses the `_numpy_json_default` encoder to handle `np.int64` / `np.float64` values that pandas produces from integer-valued price data.

---

## 2. The AI "Masterclass" Philosophy

The AI does not "guess." It strictly follows the **4-Participant Model** to construct a narrative.

### A. The 4 Participants
Price moves due to **Absence** or **Exhaustion**, not just aggressive action.
1.  **Committed Buyers**: Patient. Build value at support. (Create "Accumulation").
2.  **Committed Sellers**: Patient. Distribute value at resistance. (Create "Distribution").
3.  **Desperate Buyers (FOMO)**: Emotional. Chase price higher. (Create "Parabolic MOves").
4.  **Desperate Sellers (Panic)**: Emotional. Dump price lower. (Create "Capitulation").

### B. The 3-Act Story (Session Arc)
The AI must analyze the day as a sequential story, not a single candlestick.
1.  **Act I (Intent - Pre-Market)**: What was the plan? (e.g., "Gap Up on News").
2.  **Act II (Conflict - RTH)**: Did the real market validate or invalidate the plan? (e.g., "Invalidated. Sellers slammed the gap immediately.").
3.  **Act III (Resolution - Post-Market)**: Who is in control at the close?

### C. News vs. Price (The "Surprise" Factor)
The AI explicitly hunts for **Disconnects**:
*   **Validation**: Bad News -> Price Drops. (Boring).
*   **Invalidation (The Signal)**: Bad News -> Price Rallies. (**Major Bullish Signal**).
    *   *Rule*: If Price ignores the News, the "Underlying Conviction" is dominant.

---

## 3. Key Data Structures (JSON Cards)

### Company Card
*   `emotionalTone`: The Micro analysis (3-Act Story).
*   `technicalStructure`: The Macro analysis (Major Zones & Pattern).
*   `screener_briefing`: The actionable "Data Packet" for Python usage (Setup_Bias, Plan A/B Levels).
*   `todaysAction`: A single-day log entry appended to the `keyActionLog`.

### Economy Card
*   `marketNarrative`: The "Why" (News) + "How" (Levels) synthesis.
*   `sectorRotation`: Tracks leading/lagging sectors using the Session Arc.
*   `indexAnalysis`: SPY/QQQ analysis using the Session Arc.

---

## 4. Developer Rules

1.  **Do NOT edit `get_or_compute_context`** casually. It protects the database bill.
2.  **Prompt Engineering**: All prompts live in `modules/ai_services.py`. If you change the logic there, update this document.
3.  **Data Integrity**: Users cannot manually edit the `todaysAction` log. It is an immutable record of the AI's daily analysis.
4.  **`keyActionLog` is append-only (IMMUTABLE)**. Both `update_company_card` and `update_economy_card` must **never** overwrite an existing log entry for a given date. If an entry already exists for `trade_date_str`, log a warning and preserve the original. If you find an `else` branch that mutates an existing entry, it is a bug.
5.  **AI JSON parsing must use `_safe_parse_ai_json`**. Never call `json.loads` directly on a raw Gemini response. The helper tries three strategies (direct parse → last fenced block → bare braces) and returns `None` on total failure, which the caller must handle with a clean exception rather than silent data loss.
6.  **`fundamentalContext.valuation` is user-managed and read-only**. After every AI update to a company card, the previous card's `valuation` value must be restored. The AI is not permitted to overwrite it with placeholder text.
7.  **`dispatch_github_action` returns a 3-tuple `(bool, str, str | None)`**. All call sites must unpack all three values. The third element is the direct Actions run URL (or `None`); callers should fall back to `ACTIONS_URL` when it is `None`.

---

## 5. Secrets Management (Infisical)

The project uses **Infisical** as the single source of truth for secrets (Turso URLs, API Keys, Webhooks).

### A. The SDK & Implementation
*   **Correct Package**: Always use `infisical-sdk`. **DO NOT** use the deprecated `infisical-python` package.
*   **Manager Pattern**: All logic is encapsulated in `modules/core/infisical_manager.py`. It initializes the client and handles authentication state.
*   **Usage**: `config.py` initializes the manager and fetches secrets during application startup.

### B. Authentication Methods
The manager supports two distinct authentication flows via environment variables:
1.  **Service Token (Legacy/Simple)**:
    *   Requires: `INFISICAL_TOKEN`.
    *   Auth Call: `client.auth.login(token=INFISICAL_TOKEN)`.
2.  **Universal Auth (Machine Identity - Preferred)**:
    *   Requires: `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`.
    *   Auth Call: `client.auth.universal_auth.login(client_id=..., client_secret=...)`.
*   **Required for both**: `INFISICAL_PROJECT_ID`.

### C. Secret Retrieval Flow
To fetch a secret, the manager uses:
```python
secret = client.secrets.get_secret_by_name(
    secret_name="NAME",
    project_id=PROJECT_ID,
    environment_slug="dev",
    secret_path="/"
)
```
*   **Environment**: Defaults to `dev`.
*   **Fallback Logic**: The system is designed with a "Waterfall Fallback":
    1. Try Infisical (Exact Name).
    2. Try Infisical (Simplified Name).
    3. Try local Environment Variables (`os.getenv`).

### D. GitHub Actions Integration
Secrets must be passed to the runner via the `env` block in the workflow YAML.
```yaml
env:
  INFISICAL_CLIENT_ID: ${{ secrets.INFISICAL_CLIENT_ID }}
  INFISICAL_CLIENT_SECRET: ${{ secrets.INFISICAL_CLIENT_SECRET }}
  INFISICAL_PROJECT_ID: ${{ secrets.INFISICAL_PROJECT_ID }}
```
If these are missing, the app logs a warning and enters "Offline/Legacy Mode."

---

## 6. Deployment Architecture

### A. Discord Bot — Railway (Python 3.13)
*   **Dockerfile**: `discord_bot/Dockerfile`. Uses the **repo root** as build context so both `discord_bot/` and `modules/` are available at runtime.
*   **Railway Settings** (critical):
    *   **Root Directory** → `/` (repo root, **not** `discord_bot/`).
    *   **Dockerfile Path** → `discord_bot/Dockerfile`.
    *   Setting Root Directory to `discord_bot/` will break the build because `modules/` lives at the repo root and won't be included in the build context.
*   **Import Convention**: All intra-bot imports must use **plain imports** (`from config import ...`, `from ui_components import ...`), **never** package-qualified imports (`from discord_bot.config import ...`). The Dockerfile sets `WORKDIR /app/discord_bot` so plain imports resolve, and `bot.py` adds the parent (`/app/`) to `sys.path` for `modules.*` access.
*   **Python Version**: Railway runs **Python 3.13**. All dependencies in `discord_bot/requirements.txt` must be 3.13-compatible.
*   **Dependencies**: Managed separately in `discord_bot/requirements.txt` (not the root `requirements.txt`).

### B. Main Pipeline — GitHub Actions
*   **Entry Point**: `main.py` at the repo root.
*   **Orchestration**: The Discord Bot dispatches GitHub Actions workflows (`manual_run.yml`) for heavy compute (card building).
*   **Secrets**: Passed via `env` block in the workflow YAML from GitHub repository secrets.

---

## 7. CLI Operational Mandates (Gemini CLI ONLY)

The following rules apply **EXCLUSIVELY** to the **Gemini CLI** agent (this interface). They do **NOT** apply to automated agents like Antigravity.

1.  **Automatic Pushing**: Because all actions in the Gemini CLI are directed and approved by the user in real-time, the agent must **always** execute a `git push` immediately after completing a code modification or bug fix. 
2.  **No Manual Staging Required**: The agent should assume that once a task is finished, the state is ready for the remote repository.

---

## 8. Engineering Log

This section records resolved bugs and structural changes for traceability. Newest entries first.

### 2026-02-28 — Key Manager Checkout/Checkin Fix + Quality Tuning + Inspect Improvements

#### Key Manager — Checkout/Checkin Pattern (`modules/core/key_manager.py`)
*   **Root cause**: `get_key()` returned the key to the caller AND re-added it to `available_keys` in the same call. With parallel threads (`ThreadPoolExecutor`), multiple threads could check out the same key simultaneously before `report_usage()` recorded the token consumption. This caused cascading 429 errors as all 19 threads burned through all 46 keys in seconds.
*   **Fix**: `get_key()` no longer re-adds the key to the pool. The key stays "checked out" until explicitly returned via `report_usage()` (success) or `report_failure()` (error). This is a standard checkout/checkin pattern that prevents concurrent use of the same key.
*   **`report_usage()` now returns key to pool**: After recording token usage in the DB, the key is appended back to `available_keys`. If the DB write fails, the key is still returned to avoid key leaks.

#### Expired Key Retirement (`modules/ai/ai_services.py`)
*   **Root cause**: When Gemini returned HTTP 400 "API key expired" / "API_KEY_INVALID", the key was reported as `is_info_error=True`, which put it right back in the available pool. The same expired key would be tried over and over, consuming retry attempts.
*   **Fix**: The 400 handler now detects "API_KEY_INVALID" or "API key expired" in the response body and calls `report_fatal_error(key)` to permanently retire the key for the session. The key goes into `dead_keys` and is never returned to any thread.
*   **Retirement scope**: Session-only. On the next run, `_refresh_keys_from_db()` reloads all keys from `gemini_api_keys` and resets `dead_keys = set()`. If the key has been renewed in Google Cloud Console, it will work on the next run automatically. No manual un-retirement needed.

#### Concurrency Reduction (`main.py`)
*   **Change**: `ThreadPoolExecutor(max_workers=min(len(tickers), 20))` reduced to `max_workers=min(len(tickers), 5)`. At most 5 keys are checked out simultaneously, leaving the remaining ~41 keys as backup for retries. This prevents the cascade where all keys enter cooldown at the same time.

#### todaysAction Character Limit Relaxed (`quality_validators.py`, `ai_services.py`)
*   **Change**: Increased from 500 → 1200 characters. The 500-char limit was cutting off sentences mid-thought. The AI prompt now says "max 4-5 sentences, under 1200 chars" instead of "max 2-3 sentences, under 500 chars".
*   **Updated in**: Validator threshold, company card prompt constraint, company card JSON template, economy card system prompt, and boundary tests (1200/1201 edge cases).

#### Inspect Command Improvements (`modules/data/inspect_db.py`)
*   **Missing tickers**: Now queries `aw_ticker_notes` (stocks only, not ETFs) to determine the expected ticker list. Compares against `aw_company_cards` for the target date and explicitly lists missing tickers with count: `⚠️ Missing Tickers (6): ABT, ADBE, ...`. Shows `X/Y` format (e.g., `Updated Tickers (13/19)`).
*   **Market news detail**: Instead of just "✅ PRESENT", now shows row count and character count: `Market News: ✅ PRESENT — 1 row(s), 12,847 chars`.

### 2026-02-28 — AI Output Quality Validation Framework

#### Quality Validators (`modules/ai/quality_validators.py`) — NEW
*   **Purpose**: Reusable validator library that checks AI-generated cards against quality rules.
*   **Architecture**: `QualityReport` / `QualityIssue` dataclasses. Two public entry points: `validate_company_card(card, ticker)` and `validate_economy_card(card)`.
*   **10+ validator categories**: Schema completeness, placeholder detection, todaysAction length/card-dump detection, confidence format, screener briefing keys, emotionalTone 3-Act structure, 4-Participant terminology, trade plan price levels, content substance, valuation preservation.
*   **Production integration**: Both `update_company_card()` and `update_economy_card()` in `ai_services.py` run validators after every card generation. Results are logged to AppLogger and TRACKER but never block card return (observability-only).

#### Quality Test Suite (`tests/test_ai_quality.py`) — NEW
*   44 tests with realistic fixtures (good cards, bad card-dump, bad placeholders, missing fields, edge cases).
*   Boundary tests for todaysAction character limit (1200/1201 chars).

### 2026-02-28 — Thread Safety for Parallel Execution

#### Thread Safety (`key_manager.py`, `tracker.py`, `logger.py`)
*   **`KeyManager`**: Added `threading.Lock` protecting `get_key()`, `report_usage()`, `report_failure()`, `report_fatal_error()`.
*   **`ExecutionTracker`**: Added `threading.Lock` protecting `log_call()`, `log_error()`, `set_result()`, `register_artifact()`.
*   **`AppLogger`**: Added `threading.Lock` protecting all `self.logs` list operations.
*   **Tests**: 3 thread-safety tests added to `test_key_manager.py` (concurrent get_key, concurrent report_usage, concurrent report_failure).

### 2026-02-28 — `main.py` Architecture Overhaul + Missing DB Functions

#### `main.py` — Full Rewrite
*   **Dead import removed**: `from modules.data.data_processing import generate_analysis_text` referenced a deleted module (`data_processing.py`). Removed; ETF evidence is now computed internally by `update_economy_card` via the Impact Engine.
*   **New import**: `from modules.analysis.impact_engine import get_latest_price_details` — used for SPY market-data validation before economy card updates.
*   **`run_update_economy` now returns `bool`**: Returns `True` on successful save, `False` on any failure (missing news, missing market data, AI failure, DB save failure). Added SPY price validation gate to prevent economy updates when market data is absent.
*   **`run_update_company` extracted**: New standalone function `run_update_company(date, model, tickers, logger) -> bool` handles company card updates for a list of tickers. Returns `True` if any ticker succeeded, `False` if all failed.
*   **`send_webhook_report` promoted to module-level**: Previously a nested closure inside `main()`. Now a proper top-level function with signature `(webhook_url, target_date, action_type, model_name, logger=None)`. Sends dashboard embed first, then log/artifact files in a second request. Skips file uploads for `inspect` and `input-news` actions.
*   **`target_date` safety**: Initialised to `None` before the try block; webhook send is guarded with `target_date is not None`.
*   **`update-company` CLI action added**: New `--action update-company` option for standalone company card updates.

#### `modules/data/db_utils.py` — Missing Functions Added
*   **`update_ticker_notes(ticker, notes) -> bool`**: Upserts historical level notes into `aw_ticker_notes`. Required by `discord_bot/bot.py` for the `!editnotes` command.
*   **`get_ticker_stats() -> list[dict]`**: Returns all tracked tickers with their last company card update date. Required by `discord_bot/bot.py` for the `!listcards` command.

#### `discord_bot/__init__.py` — Created
*   Added empty `__init__.py` to make `discord_bot` a proper Python package. Required for test imports using `import discord_bot.bot`.

#### `modules/core/config.py` — Minor Cleanup
*   Replaced module-level `logger` variable with direct `logging.*` calls to avoid shadowing issues.

#### Test Suite
*   All 182 tests pass (`python3 -m pytest tests/`).

### 2026-02-26 — `inputnews` Command Hardening (discord_bot/bot.py)
*   **URL regex path truncation**: `r'https?://(?:[-\w.]|...)'` excluded `/` from its character class, so every URL was captured up to the first slash (domain only). This broke Pastebin raw-URL rewriting and any path-based URL. Fixed to `r'https?://[^\s<>"\']+'`.
*   **`aiohttp` timeout type**: `session.get(url, timeout=30)` raised `ValueError` (aiohttp requires `ClientTimeout`, not an int). Fixed to `aiohttp.ClientTimeout(total=30)`.
*   **Attachment safety**: Added 5 MB size guard before `attachment.read()`; changed `.decode("utf-8")` to `.decode("utf-8", errors="replace")` to survive non-UTF-8 news files.

### 2026-02-26 — Four Core Bug Fixes + Test Suite

#### Bug 1 — Cache Staleness (`modules/analysis/impact_engine.py`)
*   **Root cause**: `get_or_compute_context` wrote any result (including `{"status": "No Data"}`) to disk and then blindly served it forever on every subsequent call.
*   **Fix**: Added `_is_valid_context()` gate on both cache reads and writes. Stale / corrupt files are removed from disk before re-computing. Added `_numpy_json_default()` encoder to handle numpy scalar types from pandas aggregations.

#### Bug 2 — `keyActionLog` Immutability Violation (`modules/ai/ai_services.py`)
*   **Root cause**: Both `update_company_card` and `update_economy_card` had an `else` branch that iterated the log and overwrote the existing entry for the same date, violating the append-only rule in Section 4.
*   **Fix**: The `else` branch now logs `⚠️ IMMUTABILITY: ... Preserving original entry` and does nothing.

#### Bug 3 — JSON Parsing Vulnerability (`modules/ai/ai_services.py`)
*   **Root cause**: `update_economy_card` had no markdown stripping at all; `update_company_card` used a lazy regex that could grab an incomplete JSON object from earlier in the prompt string.
*   **Fix**: Added `_safe_parse_ai_json(text)` shared utility (3-strategy: direct `json.loads` → last fenced block → bare braces). Both card functions now use it exclusively.

#### Bug 4 — Silent Fire-and-Forget Dispatch (`discord_bot/bot.py`)
*   **Root cause**: `dispatch_github_action` returned `(True, "Success")` with no response body on error, and gave no confirmation URL when the dispatch succeeded.
*   **Fix**: Returns `(bool, str, str | None)` 3-tuple. Error responses include up to 300 chars of the response body. Success path polls GitHub once (after a 5 s delay) via `_fetch_latest_run_url` to retrieve the direct Actions run URL.

#### Bug 5 (discovered via tests) — `valuation` Overwritten by AI (`modules/ai/ai_services.py`)
*   **Root cause**: The `deep_update` call in `update_company_card` allowed the AI to overwrite the user's real `fundamentalContext.valuation` with its echoed placeholder text.
*   **Fix**: After `deep_update`, the previous card's `valuation` is explicitly restored.

#### Test Suite (`tests/test_fixes.py`)
*   58 tests across 9 classes covering all 5 bugs, the `_safe_parse_ai_json` helper, `_is_valid_context`, `_fetch_latest_run_url`, deep-copy isolation, and read-only field protection.
*   All 182 tests in the full suite pass (`DISABLE_INFISICAL=1 .venv/bin/python -m pytest tests/ -q`).
