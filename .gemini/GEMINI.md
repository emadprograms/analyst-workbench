# Analyst Workbench: AI Instructions & System Architecture

This document serves as the "System Knowledge Base" for the AI Agent (Antigravity) and human developers. It defines the core philosophy, infrastructure, and analytical rules engine.

---

## 1. System Architecture

The **Analyst Workbench** is a Streamlit-based Python application designed to act as an "AI Trading Assistant." It ingests raw market data, processes it via custom algorithms, and uses an LLM (Gemini) to generate actionable "Cards" for the user.

### Key Components

*   **Database (Turso/SQLite)**:
    *   `market_data`: Stores raw OHLCV price bars. (Sources: Yahoo Finance)
    *   `company_cards`: Stores the JSON output of the AI analysis (The "living memory" of the stock).
    *   `economy_cards`: Stores the JSON output of the Global Macro analysis.
    *   `daily_inputs`: Stores the daily raw news/macro context provided by the user.

*   **Computation Layer (Python)**:
    *   `modules/impact_engine.py`: The quantitative heart. Slice price action into 3 sessions (Pre, RTH, Post), detects "Impact Levels" (Support/Resistance), and tracks "Value Migration" (30min blocks).
    *   `modules/ai_services.py`: The logic layer. Constructs the massive "Masterclass" prompts, manages API keys (`KeyManager`), and parses the AI's JSON response.
    *   `app.py`: The frontend. Handles UI, user inputs, and triggers the batch update loops.

*   **Caching Layer (Context Freezing)**:
    *   **Goal**: Reduce DB reads.
    *   **Mechanism**: The `impact_engine` checks for a local file `cache/context/{ticker}_{date}.json`. If found, it loads it instantly. Use `impact_engine.get_or_compute_context`.

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
