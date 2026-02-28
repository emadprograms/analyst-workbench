"""
AI OUTPUT QUALITY TEST SUITE
==============================
Tests the quality of AI-generated cards using realistic sample data.

PURPOSE:
  - Validate that AI outputs meet structural, formatting, and content standards
  - Detect prompt regressions (when a prompt change causes quality degradation)
  - Catch known failure modes: card-dump in todaysAction, placeholder leaks,
    missing 4-Participant terminology, format violations.

USAGE:
  pytest tests/test_ai_quality.py -v            # Run all quality tests
  pytest tests/test_ai_quality.py -k "company"  # Company card tests only
  pytest tests/test_ai_quality.py -k "economy"  # Economy card tests only
  pytest tests/test_ai_quality.py -k "bad"       # Test bad-card detection

HOW TO ADD NEW REGRESSION TESTS:
  1. When you spot a bad AI output, copy the relevant fields into a new
     SAMPLE_BAD_* fixture below.
  2. Add a test that calls the validator and asserts the specific rule fires.
  3. Fix the prompt, then the test becomes your regression guard.
"""
import pytest
import os
import json
import copy

os.environ["DISABLE_INFISICAL"] = "1"

from modules.ai.quality_validators import (
    validate_company_card,
    validate_economy_card,
    QualityReport,
    QualityIssue,
)


# ==========================================
# SAMPLE FIXTURES: HIGH-QUALITY CARDS
# ==========================================

SAMPLE_GOOD_COMPANY_CARD = {
    "marketNote": "Executor's Battle Card: AAPL",
    "confidence": "Trend_Bias: Bullish (Story_Confidence: High) - Reasoning: Decisive breakout above $210 on massive volume, confirming the accumulation thesis. Price closed well above the prior resistance, establishing it as new support.",
    "screener_briefing": (
        "Setup_Bias: Bullish\n"
        "Justification: Today's Accumulation by Committed Buyers at $205 contradicts the prior choppy action, forcing a bullish bias.\n"
        "Catalyst: Post-earnings rally extended by new AI partnership announcement.\n"
        "Pattern: Breakout above $210 consolidation range, transitioning from Chop to Stable Uptrend.\n"
        "Plan_A: Long from $210 Support\n"
        "Plan_A_Level: $210\n"
        "Plan_B: Fade at $220 Resistance\n"
        "Plan_B_Level: $220\n"
        "S_Levels: [$210, $205, $200]\n"
        "R_Levels: [$220, $225, $230]"
    ),
    "basicContext": {
        "tickerDate": "AAPL | 2026-02-23",
        "sector": "Technology",
        "companyDescription": "Apple Inc. — Consumer electronics, software, and services company.",
        "priceTrend": "Strong uptrend following breakout above $210 consolidation. 3-day consecutive higher closes.",
        "recentCatalyst": "Q1 earnings beat estimates (+8% revenue YoY). New AI partnership with OpenAI announced Feb 22. iPhone 17 pre-orders exceed expectations."
    },
    "technicalStructure": {
        "majorSupport": "$210 (new tactical support — 2-day Committed Buyer defense), $205 (prior balance POC), $200 (major structural floor from Historical Notes)",
        "majorResistance": "$220 (Committed Seller zone — untested), $225 (all-time high region), $230 (measured move target)",
        "pattern": "Stable Uptrend. Price broke out of the 5-day $200-$210 consolidation range on Feb 23. Committed Sellers exhausted at $210 after 3 failed defenses. The breakout creates a new Stable Market structure with $210 as the line in the sand.",
        "keyActionLog": [
            {"date": "2026-02-20", "action": "2026-02-20: Chop (Stable). Committed Buyers defended $200 on low volume while Committed Sellers capped $208. Classic balance day with no resolution."},
            {"date": "2026-02-21", "action": "2026-02-21: Accumulation (Stable). Pre-market held at $205 support. RTH formed a higher low at $206.50, proving Committed Buyers competing for price. Post-market held gains. Seller exhaustion visible at $208."},
            {"date": "2026-02-22", "action": "2026-02-22: Continuation (Stable). Gap open to $208.50, RTH held the gap and probed $210 resistance twice. Closed at $209.80 — just below resistance but Committed Buyers in control. Setting up for a breakout test."},
            {"date": "2026-02-23", "action": "2026-02-23: Breakout (Stable to Unstable). Pre-market gapped to $211 on AI partnership news. RTH confirmed with high-volume continuation to $214. Post-market held $213. Committed Sellers absent above $210 — new support established."}
        ],
        "volumeMomentum": "Extreme volume confirmation. The breakout above $210 occurred on 2.3x average volume with the POC migrating to $212.50 (above the breakout level). Key volume event: massive 1.5M share spike at $211 in the first 15 minutes confirming Desperate Buyer (FOMO) presence. Value Area fully above $210."
    },
    "fundamentalContext": {
        "valuation": "28x forward P/E, premium to sector median of 25x",
        "analystSentiment": "Strong Buy — Goldman upgraded to $240 PT on Feb 22 following AI deal. 85% Buy ratings.",
        "insiderActivity": "CFO sold 50,000 shares at $208 on Feb 19 (pre-planned 10b5-1). No material signal.",
        "peerPerformance": "Outperforming sector (+2.3% vs XLK +0.8%). Leading mega-cap tech on the day alongside MSFT."
    },
    "behavioralSentiment": {
        "buyerVsSeller": "Committed Buyers overwhelmed Committed Sellers at $210. The 3-day accumulation pattern at $205-$208 proves buyers were competing for price, and the breakout day confirms seller exhaustion at $210. Desperate Buyers (FOMO) joined on the AI news, accelerating the move.",
        "emotionalTone": "Breakout (Stable to Unstable) - Reasoning: **(Act I)** Pre-market gapped above $210 resistance on AI partnership catalyst, signaling intent to break the 5-day range. **(Act II)** RTH confirmed the gap immediately — no backfill. Value migrated HIGHER into a 'Wide Expansion' range ($211-$214) on 2.3x volume, proving this was commitment, not just a gap. **(Act III)** Post-market held $213 with no sell-off, confirming Committed Sellers are absent above $210. The transition from 'Stable Accumulation' to 'Unstable Breakout' is confirmed.",
        "newsReaction": "Bullish Validation — AI partnership news was the catalyst, and price reacted decisively in the expected direction. No 'disconnect' or 'surprise' here — the news aligned with the existing accumulation pattern, accelerating the inevitable breakout. Relative Strength: +2.3% vs sector +0.8%."
    },
    "openingTradePlan": {
        "planName": "Long from $210 New Support",
        "knownParticipant": "Committed Buyers at $210 — confirmed by 3-day accumulation and high-volume breakout",
        "expectedParticipant": "Desperate Buyers (FOMO) on continuation above $215",
        "trigger": "$210-$211 holds as support on a pullback with a higher low forming",
        "invalidation": "$209.50 close below breakout level — negates the breakout thesis"
    },
    "alternativePlan": {
        "planName": "Fade at $220 Major Resistance",
        "scenario": "First test of major resistance after breakout — Committed Sellers expected to emerge",
        "knownParticipant": "Committed Sellers at $220 — untested major level from Historical Notes",
        "expectedParticipant": "Desperate Sellers (profit-taking) if $220 rejection occurs on declining volume",
        "trigger": "$220 test followed by reversal candle on above-average volume",
        "invalidation": "$222 close above resistance on sustained volume — would mean sellers exhausted"
    }
}


SAMPLE_GOOD_ECONOMY_CARD = {
    "marketNarrative": "The dominant narrative remains the 'Soft Landing' thesis, bolstered by today's PCE data coming in at 2.5% (in-line), removing the tail risk of a hawkish Fed surprise. However, the rotation out of mega-cap tech into small caps (IWM +1.2% vs QQQ flat) signals a broadening that may foreshadow index-level stalling at resistance.",
    "marketBias": "Cautiously Bullish",
    "keyActionLog": [
        {"date": "2026-02-21", "action": "2026-02-21: Risk-On Continuation. Markets rallied on strong jobs data, SPY pushed to new highs above $585."},
        {"date": "2026-02-22", "action": "2026-02-22: Rotation Day. SPY flat but IWM surged 1.8%, signaling capital flowing to small caps. Bonds sold off on inflation fears."},
        {"date": "2026-02-23", "action": "2026-02-23: PCE Resolution. Inline PCE print removed tail risk. SPY held consolidation above $583. Sector rotation continues with XLI and XLF leading."}
    ],
    "keyEconomicEvents": {
        "last_24h": "PCE Price Index: 2.5% YoY (in-line, prior 2.6%). Core PCE: 2.7% (vs 2.8% exp). Personal Spending: +0.3% (slightly below +0.4% exp). Durable Goods: -1.2% (miss).",
        "next_24h": "Monday: ISM Manufacturing PMI (10:00 AM ET), Construction Spending. Tuesday: JOLTS Job Openings. This is a lighter data week leading into NFP Friday."
    },
    "sectorRotation": {
        "leadingSectors": ["XLI (Industrials +1.4%)", "XLF (Financials +1.1%)", "XLE (Energy +0.8%)"],
        "laggingSectors": ["XLK (Tech -0.2%)", "XLC (Communications -0.4%)", "XLU (Utilities -0.6%)"],
        "rotationAnalysis": "Classic late-cycle rotation: capital moving from growth (XLK, XLC) into value/cyclicals (XLI, XLF, XLE). This is consistent with a 'Soft Landing' regime where economic strength benefits cyclicals more than tech. The rotation is orderly — not panic-driven — supporting the Cautiously Bullish bias."
    },
    "indexAnalysis": {
        "pattern": "Consolidation at Highs. SPY holding above $583 support after reaching $588 highs last week. QQQ is lagging due to sector rotation out of mega-caps. The pattern resembles 'Chop' at resistance with bullish lean — Committed Sellers not yet present in force.",
        "SPY": "Holding $583 tactical support (3-day Value Area Low). Committed Buyers defending this level. $588 remains resistance (Committed Sellers zone). A close above $588 on volume triggers a Measured Move target of $595.",
        "QQQ": "Relative weakness. Lagging SPY by 40bps this week as tech rotation drags. Key support at $505 (50-day MA). Resistance remains $515. The disconnect with SPY is the sector rotation, not broad market weakness."
    },
    "interMarketAnalysis": {
        "bonds": "TLT -0.5% as yields rose 3bps on resilient economic data. 10Y yield at 4.22%. The gradual creep higher in yields is consistent with 'no imminent rate cut' positioning, but not aggressive enough to threaten equities. Watch 4.30% as a caution level.",
        "commodities": "Gold (PAXGUSDT) flat at $2,045. Oil (CL=F) +0.6% on solid demand data. No inflation alarm bells — commodities confirming the 'soft landing' rather than a supply shock.",
        "currencies": "USD (UUP) +0.2%, modest strength on strong data. EUR/USD stable at 1.084. Dollar strength is incremental, not disruptive — consistent with data-driven moves rather than panic flows.",
        "crypto": "BTC +1.8% to $96,500. Risk-on tone confirmed in crypto as speculative appetite returns. BTC approaching $100K psychological resistance — a break above would signal peak risk appetite."
    },
    "marketInternals": {
        "volatility": "VIX at 14.2 (-0.3), well below the 20 fear threshold. Complacency territory — historically this supports continuation but creates vulnerability to sudden catalysts. No immediate concern."
    }
}


# ==========================================
# SAMPLE FIXTURES: BAD / FAILING CARDS
# ==========================================

SAMPLE_BAD_COMPANY_CARD_DUMP = {
    "marketNote": "Executor's Battle Card: TSLA",
    "confidence": "Bullish",  # Missing Trend_Bias format
    "screener_briefing": "TSLA looks good",  # Missing all required keys
    "basicContext": {
        "tickerDate": "TSLA | 2026-02-23",
        "sector": "AI Updates: Set in Static Editor / Preserved",  # Placeholder leaked
        "companyDescription": "AI Updates: Set in Static Editor / Preserved",  # Placeholder leaked
        "priceTrend": "Up",  # Too short
        "recentCatalyst": "AI Updates: Set in Static Editor, AI may update if action confirms"  # Placeholder
    },
    "technicalStructure": {
        "majorSupport": "$250",
        "majorResistance": "$280",
        "pattern": "Going up",  # Too short / no substance
        "keyActionLog": [
            {"date": "2026-02-23", "action": (
                "Today's action for TSLA was characterized by a complex interplay of factors. "
                "The majorSupport at $250 held while majorResistance at $280 was tested. "
                "The screener_briefing shows Setup_Bias: Bullish with S_Levels: [$250, $245] "
                "and R_Levels: [$280, $290]. The openingTradePlan is to go long from $255 "
                "with the alternativePlan being a short at $280. The behavioralSentiment "
                "shows Committed Buyers in control with Accumulation pattern. Volume was "
                "2x average confirming the volumeMomentum thesis. The fundamentalContext "
                "valuation at 85x P/E remains elevated but justified by growth. "
                "Peer performance shows outperformance vs XLY sector."
            )}
        ],
        "volumeMomentum": "High"  # Too short
    },
    "fundamentalContext": {
        "valuation": "AI RULE: READ-ONLY (Set during initialization/manual edit)",  # Placeholder overwrite
        "analystSentiment": "Buy",
        "insiderActivity": "None",
        "peerPerformance": "Good"  # Too short
    },
    "behavioralSentiment": {
        "buyerVsSeller": "Buyers winning",  # Too short, no participant language
        "emotionalTone": "Positive",  # Missing pattern label, state, 3-Act reasoning
        "newsReaction": "Favorable"  # Too short
    },
    "openingTradePlan": {
        "planName": "Go long",
        "knownParticipant": "Bulls",  # No 4-Participant terminology
        "expectedParticipant": "More bulls",  # No 4-Participant terminology
        "trigger": "If it goes up",  # No price level
        "invalidation": "If it goes down"  # No price level
    },
    "alternativePlan": {
        "planName": "Go short",
        "scenario": "If it reverses",
        "knownParticipant": "Bears",  # No 4-Participant terminology
        "expectedParticipant": "Panic sellers",  # Close but not exact model language
        "trigger": "Rejection",  # No price level
        "invalidation": "Breakout"  # No price level
    }
}


SAMPLE_BAD_ECONOMY_CARD_PLACEHOLDER = {
    "marketNarrative": "AI Updates: The current dominant story driving the market.",  # Placeholder
    "marketBias": "Uncertain",  # Not a valid bias
    "keyActionLog": [],  # Empty
    "keyEconomicEvents": {
        "last_24h": "Some data",  # Too short
        "next_24h": "AI Updates: List of upcoming high-impact events."  # Placeholder
    },
    "sectorRotation": {
        "leadingSectors": [],  # Empty
        "laggingSectors": [],  # Empty
        "rotationAnalysis": "AI Updates: Analysis of which sectors are showing strength/weakness."  # Placeholder
    },
    "indexAnalysis": {
        "pattern": "Your new summary",  # Placeholder pattern
        "SPY": "Up",  # Too short
        "QQQ": "Down"  # Too short
    },
    "interMarketAnalysis": {
        "bonds": "TLT analysis",
        "commodities": "Gold flat",
        "currencies": "Dollar up",
        "crypto": "BTC rallied"
    },
    "marketInternals": {
        "volatility": "Low"  # Too short
    }
}


SAMPLE_COMPANY_MISSING_FIELDS = {
    "marketNote": "Executor's Battle Card: NVDA",
    "confidence": "Trend_Bias: Neutral (Story_Confidence: Medium) - Reasoning: Mixed signals.",
    # Missing: screener_briefing, basicContext, technicalStructure...
}


# ==========================================
# TESTS: GOOD COMPANY CARD (SHOULD PASS)
# ==========================================

class TestGoodCompanyCard:
    """A high-quality company card should pass all validators with no critical issues."""

    def test_good_card_passes(self):
        report = validate_company_card(SAMPLE_GOOD_COMPANY_CARD, ticker="AAPL")
        assert report.passed, f"Good card should pass.\n{report.details()}"

    def test_good_card_no_critical_issues(self):
        report = validate_company_card(SAMPLE_GOOD_COMPANY_CARD, ticker="AAPL")
        assert report.critical_count == 0, (
            f"Good card should have 0 critical issues.\n{report.details()}"
        )

    def test_good_card_json_string_input(self):
        """Validator should accept JSON string input too."""
        json_str = json.dumps(SAMPLE_GOOD_COMPANY_CARD)
        report = validate_company_card(json_str, ticker="AAPL")
        assert report.passed, f"Good card (JSON string) should pass.\n{report.details()}"

    def test_good_card_schema_complete(self):
        """All required fields should be present."""
        report = validate_company_card(SAMPLE_GOOD_COMPANY_CARD, ticker="AAPL")
        schema_issues = [i for i in report.issues if i.rule.startswith("SCHEMA_")]
        assert len(schema_issues) == 0, f"Schema issues: {schema_issues}"

    def test_good_card_no_placeholders(self):
        report = validate_company_card(SAMPLE_GOOD_COMPANY_CARD, ticker="AAPL")
        placeholder_issues = [i for i in report.issues if i.rule == "CONTENT_PLACEHOLDER"]
        assert len(placeholder_issues) == 0, (
            f"Good card should have no placeholder text.\n"
            + "\n".join(f"  {i.field}: {i.message}" for i in placeholder_issues)
        )

    def test_good_card_confidence_format(self):
        report = validate_company_card(SAMPLE_GOOD_COMPANY_CARD, ticker="AAPL")
        confidence_issues = [i for i in report.issues if i.rule.startswith("CONFIDENCE_")]
        assert len(confidence_issues) == 0, f"Confidence format issues: {confidence_issues}"

    def test_good_card_screener_complete(self):
        report = validate_company_card(SAMPLE_GOOD_COMPANY_CARD, ticker="AAPL")
        screener_issues = [i for i in report.issues if i.rule.startswith("SCREENER_")]
        assert len(screener_issues) == 0, f"Screener issues: {screener_issues}"

    def test_good_card_emotional_tone_3act(self):
        report = validate_company_card(SAMPLE_GOOD_COMPANY_CARD, ticker="AAPL")
        tone_issues = [i for i in report.issues if i.rule.startswith("TONE_")]
        assert len(tone_issues) == 0, f"Tone issues: {tone_issues}"

    def test_good_card_todays_action_concise(self):
        report = validate_company_card(SAMPLE_GOOD_COMPANY_CARD, ticker="AAPL")
        action_issues = [i for i in report.issues if i.rule.startswith("ACTION_")]
        assert len(action_issues) == 0, f"Action issues: {action_issues}"


# ==========================================
# TESTS: GOOD ECONOMY CARD (SHOULD PASS)
# ==========================================

class TestGoodEconomyCard:
    """A high-quality economy card should pass all validators with no critical issues."""

    def test_good_economy_passes(self):
        report = validate_economy_card(SAMPLE_GOOD_ECONOMY_CARD)
        assert report.passed, f"Good economy card should pass.\n{report.details()}"

    def test_good_economy_no_critical(self):
        report = validate_economy_card(SAMPLE_GOOD_ECONOMY_CARD)
        assert report.critical_count == 0, f"No critical issues expected.\n{report.details()}"

    def test_good_economy_no_placeholders(self):
        report = validate_economy_card(SAMPLE_GOOD_ECONOMY_CARD)
        placeholder_issues = [i for i in report.issues if i.rule == "CONTENT_PLACEHOLDER"]
        assert len(placeholder_issues) == 0

    def test_good_economy_sectors_populated(self):
        report = validate_economy_card(SAMPLE_GOOD_ECONOMY_CARD)
        sector_issues = [i for i in report.issues if i.rule.startswith("ECON_NO_")]
        assert len(sector_issues) == 0

    def test_good_economy_bias_valid(self):
        report = validate_economy_card(SAMPLE_GOOD_ECONOMY_CARD)
        bias_issues = [i for i in report.issues if i.rule == "ECON_BAD_BIAS"]
        assert len(bias_issues) == 0


# ==========================================
# TESTS: BAD COMPANY CARD (CARD-DUMP)
# ==========================================

class TestBadCompanyCardDump:
    """
    Regression test for the 'todaysAction card-dump' bug.
    The AI was dumping the entire card analysis into todaysAction.
    """

    def test_card_dump_fails_validation(self):
        report = validate_company_card(SAMPLE_BAD_COMPANY_CARD_DUMP, ticker="TSLA")
        assert not report.passed, "Card-dump card should FAIL validation."

    def test_card_dump_detected_in_todays_action(self):
        report = validate_company_card(SAMPLE_BAD_COMPANY_CARD_DUMP, ticker="TSLA")
        dump_issues = [i for i in report.issues if i.rule == "ACTION_CARD_DUMP"]
        assert len(dump_issues) > 0, "Should detect card-dump content in todaysAction."

    def test_card_dump_too_long(self):
        report = validate_company_card(SAMPLE_BAD_COMPANY_CARD_DUMP, ticker="TSLA")
        length_issues = [i for i in report.issues if i.rule == "ACTION_TOO_LONG"]
        assert len(length_issues) > 0, (
            f"todaysAction is {len(SAMPLE_BAD_COMPANY_CARD_DUMP['technicalStructure']['keyActionLog'][-1]['action'])} "
            f"chars — should be flagged as too long."
        )

    def test_placeholder_text_detected(self):
        report = validate_company_card(SAMPLE_BAD_COMPANY_CARD_DUMP, ticker="TSLA")
        placeholder_issues = [i for i in report.issues if i.rule == "CONTENT_PLACEHOLDER"]
        assert len(placeholder_issues) >= 2, (
            f"Expected multiple placeholder detections. Found {len(placeholder_issues)}"
        )

    def test_confidence_format_violated(self):
        report = validate_company_card(SAMPLE_BAD_COMPANY_CARD_DUMP, ticker="TSLA")
        confidence_issues = [i for i in report.issues if i.rule == "CONFIDENCE_NO_BIAS"]
        assert len(confidence_issues) > 0, "Should detect missing Trend_Bias format."

    def test_screener_missing_keys(self):
        report = validate_company_card(SAMPLE_BAD_COMPANY_CARD_DUMP, ticker="TSLA")
        screener_issues = [i for i in report.issues if i.rule == "SCREENER_MISSING_KEY"]
        assert len(screener_issues) >= 3, "Should detect missing screener_briefing keys."

    def test_emotional_tone_missing_pattern(self):
        report = validate_company_card(SAMPLE_BAD_COMPANY_CARD_DUMP, ticker="TSLA")
        tone_issues = [i for i in report.issues if i.rule == "TONE_NO_PATTERN"]
        assert len(tone_issues) > 0, "Should detect missing pattern label in emotionalTone."

    def test_participant_language_missing(self):
        report = validate_company_card(SAMPLE_BAD_COMPANY_CARD_DUMP, ticker="TSLA")
        participant_issues = [i for i in report.issues if i.rule == "PARTICIPANT_MISSING"]
        assert len(participant_issues) >= 2, "Should detect missing 4-Participant terminology."

    def test_plan_missing_price_levels(self):
        report = validate_company_card(SAMPLE_BAD_COMPANY_CARD_DUMP, ticker="TSLA")
        plan_issues = [i for i in report.issues if i.rule == "PLAN_NO_PRICE"]
        assert len(plan_issues) >= 2, "Should detect missing price levels in trade plans."

    def test_thin_content_detected(self):
        report = validate_company_card(SAMPLE_BAD_COMPANY_CARD_DUMP, ticker="TSLA")
        thin_issues = [i for i in report.issues if i.rule == "CONTENT_THIN"]
        assert len(thin_issues) >= 3, "Should detect multiple thin/empty fields."


# ==========================================
# TESTS: BAD ECONOMY CARD (PLACEHOLDER)
# ==========================================

class TestBadEconomyCardPlaceholder:
    """
    Regression test for placeholder text leaking into economy cards.
    The AI sometimes echoes the prompt template instead of real analysis.
    """

    def test_placeholder_card_fails(self):
        report = validate_economy_card(SAMPLE_BAD_ECONOMY_CARD_PLACEHOLDER)
        assert not report.passed, "Placeholder economy card should FAIL."

    def test_placeholder_text_detected(self):
        report = validate_economy_card(SAMPLE_BAD_ECONOMY_CARD_PLACEHOLDER)
        placeholder_issues = [i for i in report.issues if i.rule == "CONTENT_PLACEHOLDER"]
        assert len(placeholder_issues) >= 3, (
            f"Expected 3+ placeholder detections, got {len(placeholder_issues)}"
        )

    def test_empty_sectors_detected(self):
        report = validate_economy_card(SAMPLE_BAD_ECONOMY_CARD_PLACEHOLDER)
        sector_issues = [i for i in report.issues if i.rule.startswith("ECON_NO_")]
        assert len(sector_issues) >= 2, "Should detect empty leading/lagging sectors."

    def test_invalid_bias_detected(self):
        report = validate_economy_card(SAMPLE_BAD_ECONOMY_CARD_PLACEHOLDER)
        bias_issues = [i for i in report.issues if i.rule == "ECON_BAD_BIAS"]
        assert len(bias_issues) > 0, "'Uncertain' is not a valid market bias."

    def test_thin_content_detected(self):
        report = validate_economy_card(SAMPLE_BAD_ECONOMY_CARD_PLACEHOLDER)
        thin_issues = [i for i in report.issues if i.rule == "CONTENT_THIN"]
        assert len(thin_issues) >= 2, "Should detect thin content in multiple fields."


# ==========================================
# TESTS: MISSING FIELDS CARD
# ==========================================

class TestMissingFieldsCard:
    """Cards with missing required sections should fail schema validation."""

    def test_missing_fields_fails(self):
        report = validate_company_card(SAMPLE_COMPANY_MISSING_FIELDS, ticker="NVDA")
        assert not report.passed

    def test_missing_schema_issues(self):
        report = validate_company_card(SAMPLE_COMPANY_MISSING_FIELDS, ticker="NVDA")
        missing = [i for i in report.issues if i.rule == "SCHEMA_MISSING"]
        assert len(missing) >= 5, (
            f"Expected 5+ missing field detections, got {len(missing)}: "
            + ", ".join(i.field for i in missing)
        )

    def test_invalid_json_string(self):
        report = validate_company_card("this is not json", ticker="BAD")
        assert not report.passed
        assert any(i.rule == "PARSE_FAIL" for i in report.issues)


# ==========================================
# TESTS: VALUATION PRESERVATION
# ==========================================

class TestValuationPreservation:
    """The 'valuation' field is READ-ONLY and must not be overwritten by AI."""

    def test_valuation_preserved(self):
        """When previous card has real valuation, it should be preserved."""
        previous = {"fundamentalContext": {"valuation": "28x forward P/E"}}
        current = copy.deepcopy(SAMPLE_GOOD_COMPANY_CARD)
        report = validate_company_card(current, ticker="AAPL", previous_card=previous)
        val_issues = [i for i in report.issues if i.rule == "VALUATION_OVERWRITTEN"]
        assert len(val_issues) == 0

    def test_valuation_overwritten_with_placeholder(self):
        """If AI writes the placeholder instead of real valuation, it must be caught."""
        previous = {"fundamentalContext": {"valuation": "28x forward P/E"}}
        current = copy.deepcopy(SAMPLE_GOOD_COMPANY_CARD)
        current["fundamentalContext"]["valuation"] = "AI RULE: READ-ONLY (Set during initialization/manual edit)"
        report = validate_company_card(current, ticker="AAPL", previous_card=previous)
        val_issues = [i for i in report.issues if i.rule == "VALUATION_OVERWRITTEN"]
        assert len(val_issues) > 0, "Should detect valuation overwritten with placeholder."


# ==========================================
# TESTS: EDGE CASES
# ==========================================

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_dict(self):
        report = validate_company_card({}, ticker="EMPTY")
        assert not report.passed
        assert report.critical_count > 0

    def test_none_input_json_string(self):
        """None as JSON string should fail gracefully."""
        report = validate_company_card("null", ticker="NULL")
        assert not report.passed

    def test_todays_action_exactly_500_chars(self):
        """500 chars should be the boundary — exactly 500 should pass."""
        card = copy.deepcopy(SAMPLE_GOOD_COMPANY_CARD)
        # Create a 500-char action (under limit)
        action_500 = "2026-02-23: Breakout (Stable). " + "x" * (500 - len("2026-02-23: Breakout (Stable). "))
        card["technicalStructure"]["keyActionLog"][-1]["action"] = action_500
        report = validate_company_card(card, ticker="AAPL")
        length_issues = [i for i in report.issues if i.rule == "ACTION_TOO_LONG"]
        assert len(length_issues) == 0, "Exactly 500 chars should not trigger ACTION_TOO_LONG."

    def test_todays_action_501_chars_fails(self):
        """501 chars should fail."""
        card = copy.deepcopy(SAMPLE_GOOD_COMPANY_CARD)
        action_501 = "2026-02-23: Breakout (Stable). " + "x" * (501 - len("2026-02-23: Breakout (Stable). "))
        card["technicalStructure"]["keyActionLog"][-1]["action"] = action_501
        report = validate_company_card(card, ticker="AAPL")
        length_issues = [i for i in report.issues if i.rule == "ACTION_TOO_LONG"]
        assert len(length_issues) > 0, "501 chars should trigger ACTION_TOO_LONG."

    def test_economy_card_json_string(self):
        """Economy validator should accept JSON string."""
        json_str = json.dumps(SAMPLE_GOOD_ECONOMY_CARD)
        report = validate_economy_card(json_str)
        assert report.passed, f"Good economy card (JSON string) should pass.\n{report.details()}"


# ==========================================
# TESTS: REPORT API
# ==========================================

class TestQualityReportAPI:
    """Test the QualityReport object itself."""

    def test_passed_with_no_issues(self):
        report = QualityReport(card_type="company", ticker="TEST")
        assert report.passed

    def test_passed_with_only_warnings(self):
        report = QualityReport(card_type="company", ticker="TEST")
        report.issues.append(QualityIssue("TEST", "warning", "field", "msg"))
        assert report.passed  # warnings don't fail

    def test_failed_with_critical(self):
        report = QualityReport(card_type="company", ticker="TEST")
        report.issues.append(QualityIssue("TEST", "critical", "field", "msg"))
        assert not report.passed

    def test_summary_format(self):
        report = QualityReport(card_type="company", ticker="AAPL")
        assert "COMPANY" in report.summary()
        assert "AAPL" in report.summary()

    def test_details_includes_all_issues(self):
        report = QualityReport(card_type="company", ticker="TEST")
        report.issues.append(QualityIssue("R1", "critical", "f1", "message1"))
        report.issues.append(QualityIssue("R2", "warning", "f2", "message2"))
        details = report.details()
        assert "message1" in details
        assert "message2" in details
        assert "🔴" in details
        assert "🟡" in details
