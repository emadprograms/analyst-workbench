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

*   **Impact Context Computation**:
    *   `get_or_compute_context(client, ticker, date_str, logger)` always fetches fresh data from the database and computes the context card. No local caching — every call goes to the DB to ensure data freshness.
    *   **Validation Guard (`_is_valid_context`)**: After computation, checks that the result has `status != "No Data"` AND `meta.data_points > 0`. Invalid results are logged but still returned to the caller.

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

1.  **`get_or_compute_context`** always queries the DB directly for fresh data. There is no local caching layer.
2.  **Prompt Engineering**: All prompts live in `modules/ai_services.py`. If you change the logic there, update this document.
3.  **Data Integrity**: Users cannot manually edit the `todaysAction` log. It is a system-managed record of the AI's daily analysis.
4.  **`keyActionLog` overwrites on same-date re-run**. Both `update_company_card` and `update_economy_card` use a find-or-append strategy: if an entry already exists for `trade_date_str`, it is **overwritten** with the latest AI output so the user always sees fresh analysis. Entries for other dates are never touched. This is intentional — re-running a card for the same date should replace stale data, not duplicate it.
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

### 2026-03-01 — Economy Card Prompt Rebuild (Company Card Pattern Alignment)

#### Economy Card Prompt Overhaul (`modules/ai/ai_services.py`)
*   **Root cause**: The economy card prompt was minimal — it told the AI to "follow the exact JSON schema provided in the system prompt" but the system prompt never provided the JSON schema. Unlike the company card (which has a detailed "Masterclass" prompt with explicit JSON format, field-by-field execution tasks, and anti-degeneration rules), the economy card relied on Gemini to infer the structure from the `response_schema` kwarg alone. Since `responseSchema` is intentionally NOT enforced in the API payload (to prevent Flash model cognitive overload — see 2026-02-28 entry), the model had no format guidance and frequently returned JSON missing the `todaysAction` field, causing the "AI response is missing required fields" error.
*   **Fix**: Rebuilt the economy card prompt to match the company card pattern:
    1. **System prompt**: Rewrote to be a clear role assignment with output format expectations (no schema reference).
    2. **Analytical Framework**: Added a structured 5-part framework (Two-Source Synthesis, Sector Rotation, Index Analysis, Inter-Market Analysis, Market Internals) giving the AI clear analytical guidance — mirroring the company card's "Masterclass" section.
    3. **Explicit JSON template**: Added the exact JSON output format directly in the prompt body (with `{{ }}` escaping for f-string compatibility), matching the company card's approach. Each field has a descriptive placeholder showing the AI what is expected.
    4. **Field-by-field execution tasks**: Added 8 numbered tasks explaining exactly what the AI should write in each field, with examples and rules.
    5. **`todaysAction` hardening**: Added the same anti-degeneration rules and character limit (1200 chars) that were proven effective in the company card prompt.
    6. **Debug output on failure**: Added `logger.log_code(json.dumps(ai_data, indent=2))` when `todaysAction` is missing, matching the company card's debug output pattern so the raw AI JSON is visible in logs for diagnosis.
*   **No schema enforcement change**: The `response_schema` kwarg is still passed to `call_gemini_api` for the `responseMimeType: application/json` trigger, but `responseSchema` is still NOT injected into the API payload. The AI follows the format from the prompt itself.
*   **Validators**: Economy card quality validators (`validate_economy_card`) and data validators (`validate_economy_data`) were already in place and require no changes. They continue to run after successful card generation.

### 2026-03-01 — Cache Layer Removal

#### Removed Local File Caching (`modules/analysis/impact_engine.py`)
*   **Reason**: The caching layer (`cache/context/{ticker}_{date}.json`) served stale data when the database was updated for an existing date. Users had to manually delete cache files to get fresh results, which was confusing and error-prone.
*   **Change**: `get_or_compute_context` now always fetches from the database and computes fresh context. The `_is_valid_context` guard is retained for logging when no data is available. The `_numpy_json_default` encoder is retained for other serialization needs.
*   **Tests updated**: Removed `TestCaching` (cache hit/miss tests) from `test_impact_engine.py` and `TestCacheStaleness` (5 cache integration tests) from `test_fixes.py`. Replaced with `TestGetOrComputeContext` tests that verify every call hits the DB.
*   **GEMINI.md**: Removed "Caching Layer (Context Freezing)" section. Updated developer rule #1.

### 2026-02-28 — Impact Engine Date Synchronization

#### Target Date Mismatch Fix (`modules/analysis/impact_engine.py`)
*   **Root cause**: The Impact Context Card was deriving its `meta.date` from the first row of the returned price DataFrame (`df['dt_eastern'].iloc[0]`). If a user requested an update for a date with no new data (e.g., during a holiday or late-night run), the database query naturally fell back to the previous session's data, causing the Context Card to stamp itself with yesterday's date. This caused the new `DATA_CONTEXT_DATE_MISMATCH` validator to throw warnings, as the card's target date and the math data's date diverged.
*   **Fix**: Modified `analyze_market_context` and `get_or_compute_context` to explicitly pass and use the requested `date_str` as the `meta.date` in the final JSON card, ensuring the math context is always definitively bound to the date the pipeline is executing for.

### 2026-02-28 — Flash Model Cognitive Overload Fix (Schema vs. Speed)

#### API Guardrails & Schema Removal (`modules/ai/ai_services.py`)
*   **Root cause**: The introduction of Google's strict `responseSchema` forced the Gemini Flash backend to validate every single token it generated against a complex JSON tree. When combined with a 125,000+ token input (raw news), the model suffered massive cognitive overload. This resulted in severely degraded processing speeds, infinite text loops (`\r\n\r\n...` or repeating strings), and premature connection closures.
*   **Fix**: Removed `responseSchema` from the `generationConfig`. The model now operates as a fluid text generator (like the original Streamlit version) but relies on `responseMimeType: application/json` to prevent markdown ticks.
*   **Hardware Guardrails Added**: Injected `"temperature": 0.1` into the payload to force deterministic, robotic outputs, significantly reducing hallucinated trading terminology.

### 2026-02-28 — Prompt Structural Overhaul & Context Integration

#### Prompt Restructuring ("Lost in the Middle" Fix) (`modules/ai/ai_services.py`)
*   **Root cause**: The AI was fed thousands of tokens of raw JSON data first, and given its execution instructions last, leading to attention fatigue and rule-breaking (hallucinating price levels, ignoring character limits).
*   **Fix**: Flipped the prompt structure. The AI now reads the "Masterclass" rules, analytical framework, and exact JSON output schema **first**. It is then instructed to apply those rules to the `--- START OF DATA ---` block appended at the very end.

#### `todaysAction` Degeneration Fix v2 (`modules/ai/ai_services.py`)
*   **Root cause**: Placing `todaysAction` at the very end of the JSON schema caused the Gemini Flash model to "fall off a cliff," resulting in infinite text loops, raw JSON bleeding into the string, and parroting prompt instructions back to the user.
*   **Fix**: Moved the `todaysAction` field up into the middle of the JSON schema (before `openingTradePlan`). Replaced negative constraints ("Do NOT say X") with strict positive constraints. Deleted the counterproductive `BAD Example` block from the instructions.

#### Economy Card Context Integration (`main.py`, `modules/ai/ai_services.py`)
*   **Root cause**: The Company Card AI was completely blind to the macro environment. It had to guess the market sentiment solely from raw news snippets, making its `newsReaction` (Relative Strength/Weakness) analysis highly inaccurate.
*   **Fix**: `main.py` now fetches the `economy_card_json` for the target date and passes it to `update_company_card`. This card is injected at the very top of the `--- START OF DATA ---` block, anchoring the AI's macro view before it analyzes the individual stock.
*   **Safety**: Added a hard halt in `main.py`: if a Company Card update is requested but no Economy Card exists for that date, the pipeline immediately aborts to prevent generating "blind" cards.

#### Terminal Quality Output (`modules/ai/ai_services.py`)
*   **Change**: Modified the quality gate logging. Instead of just printing "PASS with 2 warnings", the pipeline now prints the exact warning messages (e.g., `[PARTICIPANT_MISSING] expectedParticipant: Expected 4-Participant Model terminology...`) directly to the terminal for immediate developer visibility.

### 2026-02-28 — todaysAction Prompt Hardening + ALL Ticker Fix + Adaptive Workers

#### todaysAction Degeneration Fix (`modules/ai/ai_services.py`, `modules/ai/quality_validators.py`)
*   **Root cause**: The model entered a repetition/degeneration loop, producing thousands of "End. End. End. JSON ready. End of process." tokens in the `todaysAction` field. The old limit of 5000 chars was too permissive and the prompt lacked explicit anti-repetition instructions.
*   **Fix (Prompt)**: Rewrote the `todaysAction` instruction in both company and economy card prompts. New hard limit: **500 characters, exactly 2-3 sentences**. Added explicit anti-degeneration rules: "Do NOT add meta-commentary like 'End of record', 'Analysis complete', 'JSON ready'", "Do NOT loop or repeat yourself. If you find yourself writing the same idea twice, STOP."
*   **Fix (Validator)**: `_check_todays_action_quality` limit reduced from 5000 to 500 chars. Added new `ACTION_DEGENERATION` critical rule that detects 3+ occurrences of sign-off phrases like "End.", "JSON ready", "End of process", "Analysis complete".

#### "ALL" Ticker Bug (`main.py`)
*   **Root cause**: Passing `--tickers all` from the GitHub Actions workflow or CLI was treated literally — the ticker "ALL" (Allstate Corp NYSE symbol) was created as a company card entry. There was no special-case handling for "all" meaning "all stock tickers."
*   **Fix**: `main.py` CLI now checks if `raw_tickers == ["ALL"]` and expands it to the full stock ticker list from `get_all_tickers_from_db()` (excluding ETFs). Mixed inputs like `AAPL,ALL,MSFT` are NOT expanded.

#### Adaptive max_workers (`main.py`, `modules/core/key_manager.py`)
*   **Root cause**: `ThreadPoolExecutor(max_workers=min(len(tickers), 5))` was hardcoded to 5. With only 1 paid key, 4 out of 5 threads would immediately fail because the key was checked out by thread #1.
*   **Fix**: Added `KeyManager.get_tier_key_count(tier)` method that counts non-dead keys for a given tier. `run_update_company()` now uses `max_workers = max(1, min(key_count, 5))`, ensuring single-key scenarios run sequentially while multi-key setups still benefit from parallelism.

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

#### Data Validators (`modules/ai/data_validators.py`) — NEW
*   **Purpose**: Cross-references AI-generated card claims against real Impact Engine market data. While Quality Validators check structure/format, Data Validators fact-check the AI's analytical claims.
*   **Architecture**: `DataReport` / `DataIssue` dataclasses (mirrors Quality Validators pattern). Two public entry points: `validate_company_data(card, impact_context, ticker, trade_date)` and `validate_economy_data(card, etf_contexts, trade_date)`.
*   **4 validator categories**:
    1. **Directional/Bias Claims**: Detects contradictions between the AI's stated bias (bullish/bearish) and the actual price return. Uses a 5% threshold for contradictions and 2% for warnings.
    2. **Session Arc Claims**: Validates gap-up/gap-down claims against actual open vs previous close, checks "higher lows" / "held support" claims against real session price data.
    3. **Volume Claims**: Cross-references "heavy volume" / "light volume" / "volume surge" claims against actual session volume data.
    4. **Date/Ticker Consistency**: Ensures the card references the correct ticker symbol and trade date, not stale data from a previous run.
*   **Economy card validation**: Checks date consistency across the economy card, and validates macro bias claims against SPY return data.
*   **Production integration**: Both `update_company_card()` and `update_economy_card()` in `ai_services.py` run data validators after the Quality Gate. Results logged to AppLogger and `TRACKER.log_data_accuracy()`. Non-blocking (observability-only).
*   **Tracker integration**: `ExecutionTracker` gained a `data_reports` field and `log_data_accuracy()` method to record per-ticker data validation results alongside existing quality reports.

#### Data Validator Test Suite (`tests/test_data_validators.py`) — NEW
*   51 tests across 7 classes: TestHelpers, TestBiasValidation, TestSessionArcValidation, TestVolumeValidation, TestDateTickerConsistency, TestEconomyCardValidation, TestEdgeCases.
*   Full coverage of threshold boundaries, missing data graceful handling, and cross-field claim detection.

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

#### Bug 2 (Revised) — `keyActionLog` Same-Date Overwrite (`modules/ai/ai_services.py`)
*   **Original fix**: The `else` branch was made immutable — it logged a warning and preserved the original entry.
*   **Revised behaviour (2026-02-28)**: Immutability was intentionally reverted. Re-running a card for the same `trade_date_str` now **overwrites** the existing entry's `action` field with the latest AI output. Entries for other dates remain untouched. The `else` branch logs `🔄 OVERWRITING: ...` for traceability. This ensures users always see the freshest analysis when they re-run.

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

### 2026-02-28 — KeyManager Rate Limit Crisis: Root Cause Fix & Test Hardening

The KeyManager was silently broken in multiple places, causing massive 429 rate-limit cascades
(46 429s, 17 timeouts, only 5/25 successes in a single run). The test suite (229 tests) was
passing because it never validated the critical invariants that were violated.

#### Root Cause Analysis (5 bugs found by comparing against old working code)

1. **Progressive cooldown REMOVED** (`key_manager.py: report_failure`): Escalating penalties
   (`{1: 10, 2: 60, 3: 300, 4: 3600}`) were replaced with a flat 60s penalty and no strike
   tracking. Bad keys got recycled every 60s regardless of how many times they failed.

2. **Strikes check COMMENTED OUT** (`key_manager.py: _check_key_limits`): The two lines that
   blocked keys with `strikes >= MAX_STRIKES` for 24h were commented out with `# row = ...`.
   Bad keys cycled forever.

3. **Token estimation formula changed** (`key_manager.py: estimate_tokens`): Formula was
   changed from `len(text) // 4 + 1` to `int(len(text) / 2.5) + 1`, causing token estimates
   to be ~60% higher than actual. This skewed TPM pre-checks.

4. **RPD limits wrong** (`key_manager.py: MODELS_CONFIG`): Free tier `rpd` was set to `10000`
   instead of Google's actual limit of `20`. Keys could theoretically make 10,000 requests/day
   before the rate limiter kicked in, making RPD enforcement useless.

5. **Timeout handling broken** (`ai_services.py`): `ReadTimeout` exceptions fell into the
   generic `except Exception` handler with `is_info_error=True`, returning the key to the pool
   immediately with no cooldown and no token recording — despite Google having already counted
   those tokens.

#### Fixes Applied

| File | Change | Detail |
|------|--------|--------|
| `key_manager.py` | Restored progressive cooldown | `strikes = key_failure_strikes.get(key, 0) + 1; penalty = COOLDOWN_PERIODS.get(strikes, 60)` with DB persistence |
| `key_manager.py` | Uncommented strikes check | `if row['strikes'] >= MAX_STRIKES: return 86400.0` — blocks bad keys for 24h |
| `key_manager.py` | Restored token estimation | `len(text) // 4 + 1` (integer division) |
| `key_manager.py` | Fixed free tier RPD | `'rpd': 10000` → `'rpd': 20` for all free-tier models |
| `key_manager.py` | Added missing model | `gemini-2.5-flash-lite-free` with `rpm=10, tpm=250000, rpd=20` |
| `ai_services.py` | Separate ReadTimeout handler | Explicit `except requests.exceptions.ReadTimeout` with `is_info_error=False` (key gets cooldown) |
| `ai_services.py` | Increased HTTP timeout | `timeout=60` → `timeout=240` for large ~175K token requests |

#### Test Suite Hardening (36 new tests)

The existing 229 tests passed with the broken code because they never asserted on the
invariants that were violated. Added 36 targeted tests that would immediately fail if
any of these bugs were reintroduced:

**`tests/test_key_manager.py` (29 new tests):**
*   `TestModelsConfig` (7 tests): Validates free-tier RPD=20, TPM=250000, flash-lite model
    exists, all configs have required fields, COOLDOWN_PERIODS escalate, MAX_STRIKES is
    reasonable.
*   `TestProgressiveCooldown` (8 tests): Validates each strike level maps to correct penalty
    (10s/60s/300s/3600s), strikes persist across calls, info errors don't increment strikes,
    failures write strike count to DB.
*   `TestStrikesBlocking` (4 tests): Validates `_check_key_limits` returns 86400 at
    MAX_STRIKES, above MAX_STRIKES, and FATAL_STRIKE_COUNT; allows keys below threshold.
*   `TestRPDEnforcement` (3 tests): Validates RPD exceeded blocks key, under-limit allows,
    resets on new day.
*   `TestTokenEstimationFormula` (5 tests): Validates exact `//4` formula, integer division,
    and detects if the `/2.5` overestimation bug returns.
*   `TestTPMPreCheck` (2 tests): Validates TPM budget enforcement blocks/allows correctly.

**`tests/test_ai_services.py` (7 new tests):**
*   `TestTimeoutHandling`: ReadTimeout triggers `report_failure(is_info_error=False)`, generic
    exceptions use `is_info_error=True`, 400 API_KEY_INVALID triggers `report_fatal_error`,
    429 triggers real failure, 500 is info error, HTTP timeout is 120s.

#### Validation Results
*   **Tests**: 265 passed (229 original + 36 new), 0 failed.
*   **Production run** (update-company, 2026-02-17): **0 rate-limit 429s** (down from 46),
    3 timeouts (keys properly cooldown'd), 1 expired key retired, 1/1 ticker succeeded.
