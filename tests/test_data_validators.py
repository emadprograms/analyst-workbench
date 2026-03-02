"""
AI OUTPUT DATA ACCURACY TEST SUITE
====================================
Tests the data-accuracy validators that cross-reference AI-generated card
claims against real market data from the Impact Engine context card.

PURPOSE:
  - Validate that bias claims match actual price direction
  - Detect false "gap up/down" and "higher lows" claims
  - Catch volume mischaracterizations
  - Ensure date/ticker consistency

USAGE:
  pytest tests/test_data_validators.py -v
  pytest tests/test_data_validators.py -k "bias"      # Bias tests only
  pytest tests/test_data_validators.py -k "session"    # Session arc tests
  pytest tests/test_data_validators.py -k "volume"     # Volume tests
  pytest tests/test_data_validators.py -k "date"       # Date consistency tests
"""
import pytest
import os
import copy

os.environ["DISABLE_INFISICAL"] = "1"

from modules.ai.data_validators import (
    validate_company_data,
    validate_economy_data,
    DataReport,
    DataIssue,
    _extract_setup_bias,
    _get_rth_return,
    _detect_direction,
    RETURN_MAGNITUDE_TOLERANCE,
)


# ==========================================
# SAMPLE FIXTURES: Impact Engine Context Card
# ==========================================

SAMPLE_CONTEXT_CARD = {
    "meta": {
        "ticker": "AAPL",
        "date": "2026-02-23",
        "data_points": 694,
    },
    "reference": {
        "yesterday_high": 255.45,
        "yesterday_low": 253.30,
        "yesterday_close": 253.30,
        "date": "2026-02-22",
    },
    "sessions": {
        "pre_market": {
            "status": "Active",
            "high": 258.19,
            "low": 253.81,
            "volume_approx": 523103,
            "volume_profile": {
                "POC": 257.22,
                "VAH": 258.00,
                "VAL": 254.59,
            },
            "key_volume_events": [
                {"time": "09:28", "price": 257.8, "volume": 82312, "action": "Set High-of-Day | Strong Up-Bar"},
                {"time": "08:01", "price": 255.0, "volume": 45207, "action": "Set Low-of-Day | Strong Up-Bar"},
            ],
            "key_levels": [],
            "value_migration": [
                {"time": "09:00", "POC": 255.25, "nature": "Flat, Tight Range", "range": "254.80-255.38"},
                {"time": "09:30", "POC": 256.10, "nature": "Green, Tight Range", "range": "255.15-257.29"},
            ],
        },
        "regular_hours": {
            "status": "Active",
            "high": 266.29,
            "low": 255.54,
            "volume_approx": 39989281,
            "volume_profile": {
                "POC": 259.18,
                "VAH": 265.97,
                "VAL": 256.85,
            },
            "key_volume_events": [
                {"time": "09:30", "price": 256.44, "volume": 1487077, "action": "Set Low-of-Day | Strong Down-Bar"},
                {"time": "15:59", "price": 263.75, "volume": 1326942, "action": "Strong Down-Bar"},
                {"time": "09:31", "price": 257.98, "volume": 509775, "action": "Strong Up-Bar"},
            ],
            "key_levels": [
                {"type": "RESISTANCE", "rank": 1, "level": 262.62, "strength_score": 0.23},
                {"type": "SUPPORT", "rank": 1, "level": 257.61, "strength_score": 20.08},
            ],
            "value_migration": [
                {"time": "14:30", "POC": 258.90, "nature": "Green, Wide Range", "range": "255.54-259.72"},
                {"time": "15:00", "POC": 259.55, "nature": "Green, Moderate Range", "range": "258.44-261.39"},
                {"time": "15:30", "POC": 259.70, "nature": "Red, Tight Range", "range": "258.82-260.14"},
                {"time": "16:00", "POC": 260.55, "nature": "Green, Moderate Range", "range": "258.74-260.98"},
                {"time": "16:30", "POC": 260.25, "nature": "Green, Moderate Range", "range": "259.93-261.73"},
                {"time": "17:00", "POC": 261.65, "nature": "Green, Tight Range", "range": "261.16-262.23"},
                {"time": "17:30", "POC": 261.50, "nature": "Green, Moderate Range", "range": "260.91-262.74"},
                {"time": "18:00", "POC": 262.40, "nature": "Green, Moderate Range", "range": "262.07-263.69"},
                {"time": "18:30", "POC": 265.10, "nature": "Green, Moderate Range", "range": "263.32-266.11"},
                {"time": "19:00", "POC": 265.70, "nature": "Red, Tight Range", "range": "265.19-266.29"},
            ],
        },
        "post_market": {
            "status": "Active",
            "high": 265.28,
            "low": 262.57,
            "volume_approx": 1363440,
            "volume_profile": {
                "POC": 263.92,
                "VAH": 263.94,
                "VAL": 263.90,
            },
            "key_volume_events": [
                {"time": "16:00", "price": 263.88, "volume": 916367, "action": "Strong Down-Bar"},
            ],
            "key_levels": [],
            "value_migration": [
                {"time": "21:00", "POC": 264.05, "nature": "Green, Wide Range", "range": "262.57-264.55"},
                {"time": "21:30", "POC": 264.50, "nature": "Green, Wide Range", "range": "263.88-264.98"},
            ],
        },
    },
}


SAMPLE_COMPANY_CARD = {
    "marketNote": "Executor's Battle Card: AAPL",
    "confidence": "Trend_Bias: Bullish (Story_Confidence: High) - Reasoning: Decisive breakout above $260 on massive volume.",
    "screener_briefing": "Setup_Bias: Bullish\nS_Levels: [$257, $255]\nR_Levels: [$266, $270]",
    "basicContext": {
        "tickerDate": "AAPL | 2026-02-23",
        "sector": "Technology",
        "companyDescription": "Apple Inc.",
        "priceTrend": "Strong uptrend with intraday POC migrating higher throughout the session.",
        "recentCatalyst": "AI partnership announcement.",
    },
    "technicalStructure": {
        "majorSupport": "$257.61 (Committed Buyer zone), $255 (structural floor)",
        "majorResistance": "$266 (Committed Seller zone), $270 (measured move target)",
        "pattern": "Breakout above $260 consolidation. Committed Sellers exhausted.",
        "keyActionLog": [
            {"date": "2026-02-22", "action": "2026-02-22: Accumulation (Stable). Buyers defended $255."},
            {"date": "2026-02-23", "action": "2026-02-23: Breakout (Stable). Price broke $260 on high volume."},
        ],
        "volumeMomentum": "High-volume breakout. RTH volume surged with massive 1.4M share spike at the $257 reclaim, confirming Committed Buyer conviction.",
    },
    "fundamentalContext": {
        "analystSentiment": "Strong Buy",
        "insiderActivity": "No material activity.",
        "peerPerformance": "Outperforming XLK by 2%.",
    },
    "behavioralSentiment": {
        "buyerVsSeller": "Committed Buyers overwhelmed sellers at $257. Higher lows established throughout RTH.",
        "emotionalTone": "Breakout (Stable) - Reasoning: **(Act I)** Pre-market gapped up above $255 on AI news. **(Act II)** RTH confirmed with sustained buying, value migrating higher from $258 to $265. **(Act III)** Post-market held $263 with no sell-off.",
        "newsReaction": "Bullish Validation — AI partnership news drove decisive breakout.",
    },
    "openingTradePlan": {
        "planName": "Long from $260 Support",
        "knownParticipant": "Committed Buyers",
        "expectedParticipant": "Desperate Buyers",
        "trigger": "$260 holds as support on pullback",
        "invalidation": "$258 close below breakout",
    },
    "alternativePlan": {
        "planName": "Fade at $270 Resistance",
        "scenario": "First test of resistance",
        "knownParticipant": "Committed Sellers",
        "expectedParticipant": "Desperate Sellers",
        "trigger": "$270 rejection on volume",
        "invalidation": "$272 close above",
    },
}


# ==========================================
# HELPER TESTS
# ==========================================

class TestHelpers:
    """Test internal helper functions."""

    def test_extract_setup_bias_bullish(self):
        assert _extract_setup_bias("Setup_Bias: Bullish\nJustification: ...") == "Bullish"

    def test_extract_setup_bias_bearish(self):
        assert _extract_setup_bias("Setup_Bias: Bearish\nJustification: ...") == "Bearish"

    def test_extract_setup_bias_neutral(self):
        assert _extract_setup_bias("Setup_Bias: Neutral\nJustification: ...") == "Neutral"

    def test_extract_setup_bias_missing(self):
        assert _extract_setup_bias("Some random text") is None

    def test_extract_setup_bias_underscore_variant(self):
        assert _extract_setup_bias("Setup Bias: Bullish") == "Bullish"

    def test_get_rth_return_positive(self):
        """Price went up from prev close 255.30 → post-market POC 264.50 ≈ +3.6%"""
        ret = _get_rth_return(SAMPLE_CONTEXT_CARD)
        assert ret is not None
        assert ret > 3.0  # roughly +3.6%

    def test_get_rth_return_no_reference(self):
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        ctx["reference"]["yesterday_close"] = 0
        assert _get_rth_return(ctx) is None

    def test_get_rth_return_no_rth(self):
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        ctx["sessions"]["regular_hours"]["status"] = "No Data"
        ctx["sessions"]["post_market"]["status"] = "No Data"
        assert _get_rth_return(ctx) is None


# ==========================================
# BIAS / DIRECTIONAL TESTS
# ==========================================

class TestBiasValidation:
    """Test directional / bias claim validators using Setup_Bias."""

    def test_bullish_bias_matches_up_day(self):
        """Bullish bias on a day that rallied → no issues."""
        report = validate_company_data(
            SAMPLE_COMPANY_CARD, SAMPLE_CONTEXT_CARD,
            ticker="AAPL", trade_date="2026-02-23",
        )
        bias_issues = [i for i in report.issues if "BIAS" in i.rule]
        assert len(bias_issues) == 0

    def test_bullish_bias_on_big_down_day_critical(self):
        """Bullish bias on a day that dropped >5% → critical."""
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        # Set yesterday close high, making today's close a big drop
        ctx["reference"]["yesterday_close"] = 290.0  # ~290 → ~264 ≈ -9%
        report = validate_company_data(
            SAMPLE_COMPANY_CARD, ctx, ticker="AAPL", trade_date="2026-02-23",
        )
        critical = [i for i in report.issues if i.rule == "DATA_BIAS_CONTRADICTION"]
        assert len(critical) == 1
        assert critical[0].severity == "critical"
        assert "Bullish" in critical[0].message

    def test_bullish_bias_on_mild_down_day_critical(self):
        """Bullish bias on a day that dropped 2-5% → critical."""
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        # ~275 → ~264 ≈ -4%
        ctx["reference"]["yesterday_close"] = 275.0
        report = validate_company_data(
            SAMPLE_COMPANY_CARD, ctx, ticker="AAPL", trade_date="2026-02-23",
        )
        issues = [i for i in report.issues if i.rule == "DATA_BIAS_MISMATCH"]
        assert len(issues) == 1
        assert issues[0].severity == "critical"

    def test_bearish_bias_on_big_up_day_critical(self):
        """Bearish bias on a huge rally → critical."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["screener_briefing"] = "Setup_Bias: Bearish\nJustification: Breakdown below support."
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        # ~240 → ~264 ≈ +10%
        ctx["reference"]["yesterday_close"] = 240.0
        report = validate_company_data(card, ctx, ticker="AAPL", trade_date="2026-02-23")
        critical = [i for i in report.issues if i.rule == "DATA_BIAS_CONTRADICTION"]
        assert len(critical) == 1
        assert "Bearish" in critical[0].message

    def test_bearish_bias_on_mild_up_day_warning(self):
        """Bearish bias on a day that rallied 2-5% → warning."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["screener_briefing"] = "Setup_Bias: Bearish"
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        ctx["reference"]["yesterday_close"] = 257.0  # ~257 → ~264 ≈ +2.7%
        report = validate_company_data(card, ctx, ticker="AAPL", trade_date="2026-02-23")
        warnings = [i for i in report.issues if i.rule == "DATA_BIAS_MISMATCH"]
        assert len(warnings) == 1

    def test_neutral_bias_no_contradiction(self):
        """Neutral bias should never trigger bias contradictions."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["screener_briefing"] = "Setup_Bias: Neutral"
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        ctx["reference"]["yesterday_close"] = 290.0  # big drop
        report = validate_company_data(card, ctx, ticker="AAPL", trade_date="2026-02-23")
        bias_issues = [i for i in report.issues if "BIAS" in i.rule]
        assert len(bias_issues) == 0


# ==========================================
# SESSION ARC TESTS
# ==========================================

class TestSessionArcValidation:
    """Test session arc claim validators (gap, higher lows, held support)."""

    def test_gap_up_claim_valid(self):
        """Pre-market opened above prev close → 'gap up' claim is valid."""
        # Prev close: 255.30, pre-market first range starts at 254.80 → technically not a gap up
        # But let's make it clearly a gap up
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        ctx["reference"]["yesterday_close"] = 250.00  # well below pre-market open of ~254.80
        report = validate_company_data(
            SAMPLE_COMPANY_CARD, ctx, ticker="AAPL", trade_date="2026-02-23",
        )
        gap_issues = [i for i in report.issues if i.rule == "DATA_GAP_MISMATCH"]
        assert len(gap_issues) == 0

    def test_gap_up_claim_false(self):
        """Claims 'gap up' but pre-market opened flat/below → warning."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["behavioralSentiment"]["emotionalTone"] = (
            "Breakout (Stable) - Reasoning: **(Act I)** Pre-market gapped up strongly. "
            "**(Act II)** RTH confirmed. **(Act III)** Post-market held."
        )
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        ctx["reference"]["yesterday_close"] = 260.00  # pre-market open ~254.80 is below 260
        report = validate_company_data(card, ctx, ticker="AAPL", trade_date="2026-02-23")
        gap_issues = [i for i in report.issues if i.rule == "DATA_GAP_MISMATCH"]
        assert len(gap_issues) == 1
        assert "gap up" in gap_issues[0].message.lower()

    def test_gap_down_claim_false(self):
        """Claims 'gap down' but pre-market opened above prev close → warning."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["behavioralSentiment"]["emotionalTone"] = (
            "Capitulation (Unstable) - Reasoning: **(Act I)** Pre-market gapped down. "
            "**(Act II)** RTH sold off. **(Act III)** Held lows."
        )
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        ctx["reference"]["yesterday_close"] = 250.00  # pre-market open ~254.80 is above 250
        report = validate_company_data(card, ctx, ticker="AAPL", trade_date="2026-02-23")
        gap_issues = [i for i in report.issues if i.rule == "DATA_GAP_MISMATCH"]
        assert len(gap_issues) == 1
        assert "gap down" in gap_issues[0].message.lower()

    def test_no_gap_claim_no_check(self):
        """No gap claim in text → no gap issues raised."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["behavioralSentiment"]["emotionalTone"] = (
            "Accumulation (Stable) - Reasoning: Buyers defended support."
        )
        report = validate_company_data(card, SAMPLE_CONTEXT_CARD, ticker="AAPL", trade_date="2026-02-23")
        gap_issues = [i for i in report.issues if i.rule == "DATA_GAP_MISMATCH"]
        assert len(gap_issues) == 0

    def test_higher_lows_claim_valid(self):
        """RTH blocks show ascending lows → 'higher lows' claim is valid."""
        # Default SAMPLE_CONTEXT_CARD has ascending migration ranges
        report = validate_company_data(
            SAMPLE_COMPANY_CARD, SAMPLE_CONTEXT_CARD,
            ticker="AAPL", trade_date="2026-02-23",
        )
        hl_issues = [i for i in report.issues if i.rule == "DATA_HIGHER_LOWS_FALSE"]
        assert len(hl_issues) == 0

    def test_higher_lows_claim_false(self):
        """Claims 'higher lows' but RTH lows were descending → warning."""
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        ctx["sessions"]["regular_hours"]["value_migration"] = [
            {"time": "14:30", "POC": 258.90, "nature": "Red", "range": "260.00-262.00"},
            {"time": "15:00", "POC": 257.00, "nature": "Red", "range": "258.00-260.00"},
            {"time": "15:30", "POC": 256.00, "nature": "Red", "range": "256.00-258.00"},
            {"time": "16:00", "POC": 255.00, "nature": "Red", "range": "254.00-256.00"},
            {"time": "16:30", "POC": 254.00, "nature": "Red", "range": "252.00-255.00"},
            {"time": "17:00", "POC": 253.00, "nature": "Red", "range": "250.00-254.00"},
        ]
        report = validate_company_data(
            SAMPLE_COMPANY_CARD, ctx, ticker="AAPL", trade_date="2026-02-23",
        )
        hl_issues = [i for i in report.issues if i.rule == "DATA_HIGHER_LOWS_FALSE"]
        assert len(hl_issues) == 1
        assert "higher lows" in hl_issues[0].message.lower()

    def test_held_support_claim_valid(self):
        """Claims held support at $257 and RTH low was $255.54 (above $257 - 0.5% tol) → border case."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["behavioralSentiment"]["emotionalTone"] = (
            "Accumulation - Reasoning: **(Act I)** Pre-market held. "
            "**(Act II)** Committed Buyers defended $255. **(Act III)** Close held."
        )
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        # RTH low 255.54 is close to $255 → within tolerance
        report = validate_company_data(card, ctx, ticker="AAPL", trade_date="2026-02-23")
        support_issues = [i for i in report.issues if i.rule == "DATA_SUPPORT_BREACHED"]
        assert len(support_issues) == 0

    def test_held_support_claim_breached(self):
        """Claims 'defended $260' but RTH low was $250 → warning."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["behavioralSentiment"]["emotionalTone"] = (
            "Accumulation - Reasoning: Committed Buyers defended $260 with conviction."
        )
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        ctx["sessions"]["regular_hours"]["low"] = 250.00
        report = validate_company_data(card, ctx, ticker="AAPL", trade_date="2026-02-23")
        support_issues = [i for i in report.issues if i.rule == "DATA_SUPPORT_BREACHED"]
        assert len(support_issues) == 1
        assert "$260" in support_issues[0].message

    def test_held_support_at_level_claim(self):
        """Claims 'held support at $255' and RTH low was $255.54 → within tolerance, no issue."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["behavioralSentiment"]["buyerVsSeller"] = (
            "Committed Buyers held support at $255 throughout RTH."
        )
        card["behavioralSentiment"]["emotionalTone"] = (
            "Accumulation (Stable) - Reasoning: Buyers defended support zone."
        )
        report = validate_company_data(card, SAMPLE_CONTEXT_CARD, ticker="AAPL", trade_date="2026-02-23")
        support_issues = [i for i in report.issues if i.rule == "DATA_SUPPORT_BREACHED"]
        assert len(support_issues) == 0

    def test_held_support_claim_wick_allowed(self):
        """Claims 'held $260', RTH low was $250 (huge wick), but all value migration POCs were > $260 → allowed."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["behavioralSentiment"]["emotionalTone"] = (
            "Accumulation - Reasoning: Buyers successfully defended $260."
        )
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        ctx["sessions"]["regular_hours"]["low"] = 250.00
        # Force all value migration POCs to be above 260.0, so the drop to 250 was just a fast wick
        for block in ctx["sessions"]["regular_hours"]["value_migration"]:
            if block["POC"] < 260.0:
                block["POC"] = 260.50
                
        report = validate_company_data(card, ctx, ticker="AAPL", trade_date="2026-02-23")
        support_issues = [i for i in report.issues if i.rule == "DATA_SUPPORT_BREACHED"]
        assert len(support_issues) == 0  # Should allow the claim because value stayed above 260


# ==========================================
# VOLUME TESTS
# ==========================================

class TestVolumeValidation:
    """Test volume claim validators."""

    def test_high_volume_claim_no_contradiction(self):
        """Claims high volume, data shows significant volume → no critical issues."""
        report = validate_company_data(
            SAMPLE_COMPANY_CARD, SAMPLE_CONTEXT_CARD,
            ticker="AAPL", trade_date="2026-02-23",
        )
        vol_issues = [i for i in report.issues if "VOLUME" in i.rule]
        # Should not have critical volume issues
        critical_vol = [i for i in vol_issues if i.severity == "critical"]
        assert len(critical_vol) == 0

    def test_low_volume_claim_with_wide_value_area(self):
        """Claims 'low volume' but Value Area is 70%+ of range → warning."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["technicalStructure"]["volumeMomentum"] = (
            "Low volume session. Thin, unconvincing volume throughout RTH."
        )
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        # Make value area wide: VAL=256, VAH=265 on range 255.54-266.29 → ~84%
        ctx["sessions"]["regular_hours"]["volume_profile"] = {
            "POC": 260.00, "VAH": 265.00, "VAL": 256.00,
        }
        report = validate_company_data(card, ctx, ticker="AAPL", trade_date="2026-02-23")
        vol_issues = [i for i in report.issues if i.rule == "DATA_VOLUME_PROFILE_MISMATCH"]
        assert len(vol_issues) == 1
        assert "low" in vol_issues[0].message.lower()

    def test_low_volume_claim_with_high_pre_market_activity(self):
        """Claims 'low volume' but pre-market was 15%+ of RTH → info."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["technicalStructure"]["volumeMomentum"] = (
            "Light volume throughout the day. Muted volume and no conviction."
        )
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        # Make pre-market volume very high relative to RTH
        ctx["sessions"]["pre_market"]["volume_approx"] = 8000000  # 8M pre vs 40M RTH = 20%
        report = validate_company_data(card, ctx, ticker="AAPL", trade_date="2026-02-23")
        vol_issues = [i for i in report.issues if i.rule == "DATA_VOLUME_MISMATCH"]
        assert len(vol_issues) == 1
        assert "pre-market" in vol_issues[0].message.lower()

    def test_no_volume_claim_no_check(self):
        """No high/low volume language → no volume issues."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["technicalStructure"]["volumeMomentum"] = "Volume was typical for this stock."
        report = validate_company_data(card, SAMPLE_CONTEXT_CARD, ticker="AAPL", trade_date="2026-02-23")
        vol_issues = [i for i in report.issues if "VOLUME" in i.rule]
        assert len(vol_issues) == 0

    def test_volume_claims_with_no_rth_data(self):
        """No RTH session data → validator skips gracefully."""
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        ctx["sessions"]["regular_hours"]["status"] = "No Data"
        report = validate_company_data(
            SAMPLE_COMPANY_CARD, ctx, ticker="AAPL", trade_date="2026-02-23",
        )
        # Should not crash, may have other issues but no volume crash
        assert isinstance(report, DataReport)


# ==========================================
# DATE / TICKER CONSISTENCY TESTS
# ==========================================

class TestDateTickerConsistency:
    """Test date and ticker consistency validators."""

    def test_correct_ticker_and_date(self):
        """tickerDate and log date match expectations → no issues."""
        report = validate_company_data(
            SAMPLE_COMPANY_CARD, SAMPLE_CONTEXT_CARD,
            ticker="AAPL", trade_date="2026-02-23",
        )
        date_issues = [i for i in report.issues if i.rule in (
            "DATA_TICKER_WRONG", "DATA_DATE_WRONG", "DATA_LOG_DATE_STALE",
            "DATA_CONTEXT_DATE_MISMATCH", "DATA_CONTEXT_TICKER_MISMATCH",
        )]
        assert len(date_issues) == 0

    def test_wrong_ticker_in_card(self):
        """tickerDate has wrong ticker → critical."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["basicContext"]["tickerDate"] = "MSFT | 2026-02-23"
        report = validate_company_data(
            card, SAMPLE_CONTEXT_CARD, ticker="AAPL", trade_date="2026-02-23",
        )
        ticker_issues = [i for i in report.issues if i.rule == "DATA_TICKER_WRONG"]
        assert len(ticker_issues) == 1
        assert ticker_issues[0].severity == "critical"

    def test_wrong_date_in_card(self):
        """tickerDate has wrong date → critical."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["basicContext"]["tickerDate"] = "AAPL | 2026-02-22"  # yesterday
        report = validate_company_data(
            card, SAMPLE_CONTEXT_CARD, ticker="AAPL", trade_date="2026-02-23",
        )
        date_issues = [i for i in report.issues if i.rule == "DATA_DATE_WRONG"]
        assert len(date_issues) == 1
        assert date_issues[0].severity == "critical"

    def test_stale_log_date(self):
        """Latest keyActionLog entry is from wrong date → critical."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["technicalStructure"]["keyActionLog"][-1]["date"] = "2026-02-20"
        report = validate_company_data(
            card, SAMPLE_CONTEXT_CARD, ticker="AAPL", trade_date="2026-02-23",
        )
        stale_issues = [i for i in report.issues if i.rule == "DATA_LOG_DATE_STALE"]
        assert len(stale_issues) == 1
        assert stale_issues[0].severity == "critical"

    def test_context_date_mismatch(self):
        """Impact Engine context is from different date → warning."""
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        ctx["meta"]["date"] = "2026-02-22"
        report = validate_company_data(
            SAMPLE_COMPANY_CARD, ctx, ticker="AAPL", trade_date="2026-02-23",
        )
        ctx_issues = [i for i in report.issues if i.rule == "DATA_CONTEXT_DATE_MISMATCH"]
        assert len(ctx_issues) == 1

    def test_context_ticker_mismatch(self):
        """Impact Engine context is for wrong ticker → critical."""
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        ctx["meta"]["ticker"] = "GOOGL"
        report = validate_company_data(
            SAMPLE_COMPANY_CARD, ctx, ticker="AAPL", trade_date="2026-02-23",
        )
        ctx_issues = [i for i in report.issues if i.rule == "DATA_CONTEXT_TICKER_MISMATCH"]
        assert len(ctx_issues) == 1
        assert ctx_issues[0].severity == "critical"

    def test_no_trade_date_skips_date_checks(self):
        """When trade_date is empty, date checks are skipped."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["basicContext"]["tickerDate"] = "WRONG | WRONG"
        card["technicalStructure"]["keyActionLog"][-1]["date"] = "9999-99-99"
        report = validate_company_data(card, SAMPLE_CONTEXT_CARD, ticker="AAPL", trade_date="")
        date_issues = [i for i in report.issues if i.rule in (
            "DATA_TICKER_WRONG", "DATA_DATE_WRONG", "DATA_LOG_DATE_STALE",
        )]
        assert len(date_issues) == 0


# ==========================================
# ECONOMY CARD TESTS
# ==========================================

class TestEconomyCardValidation:
    """Test economy card data validators."""

    SAMPLE_ECONOMY_CARD = {
        "marketNarrative": "Risk-on tone continues.",
        "marketBias": "Cautiously Bullish",
        "keyActionLog": [
            {"date": "2026-02-23", "action": "2026-02-23: SPY held $583 support."},
        ],
        "keyEconomicEvents": {"last_24h": "PCE in line.", "next_24h": "ISM PMI."},
        "sectorRotation": {
            "leadingSectors": ["XLI"], "laggingSectors": ["XLK"],
            "rotationAnalysis": "Rotation to cyclicals.",
        },
        "indexAnalysis": {"pattern": "Consolidation", "SPY": "Holding $583.", "QQQ": "Lagging."},
        "interMarketAnalysis": {"bonds": "TLT -0.5%", "commodities": "Oil flat.", "currencies": "USD stable.", "crypto": "BTC +1.8%."},
        "marketInternals": {"volatility": "VIX at 14.2."},
    }

    def test_economy_correct_date(self):
        """Economy card with matching log date → no issues."""
        report = validate_economy_data(
            self.SAMPLE_ECONOMY_CARD, trade_date="2026-02-23",
        )
        date_issues = [i for i in report.issues if i.rule == "DATA_LOG_DATE_STALE"]
        assert len(date_issues) == 0

    def test_economy_stale_log_date(self):
        """Economy card latest log entry from wrong date → critical."""
        card = copy.deepcopy(self.SAMPLE_ECONOMY_CARD)
        card["keyActionLog"][-1]["date"] = "2026-02-20"
        report = validate_economy_data(card, trade_date="2026-02-23")
        stale_issues = [i for i in report.issues if i.rule == "DATA_LOG_DATE_STALE"]
        assert len(stale_issues) == 1
        assert stale_issues[0].severity == "critical"

    def test_economy_bullish_bias_spy_up(self):
        """Bullish bias with SPY up → no contradiction."""
        spy_ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        spy_ctx["meta"]["ticker"] = "SPY"
        report = validate_economy_data(
            self.SAMPLE_ECONOMY_CARD,
            etf_contexts={"SPY": spy_ctx},
            trade_date="2026-02-23",
        )
        bias_issues = [i for i in report.issues if "ECON_BIAS" in i.rule]
        assert len(bias_issues) == 0

    def test_economy_bullish_bias_spy_crash_critical(self):
        """Bullish bias but SPY dropped >5% → critical."""
        card = copy.deepcopy(self.SAMPLE_ECONOMY_CARD)
        card["marketBias"] = "Bullish"
        spy_ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        spy_ctx["meta"]["ticker"] = "SPY"
        spy_ctx["reference"]["yesterday_close"] = 290.0  # big drop
        report = validate_economy_data(card, etf_contexts={"SPY": spy_ctx}, trade_date="2026-02-23")
        critical = [i for i in report.issues if i.rule == "DATA_ECON_BIAS_CONTRADICTION"]
        assert len(critical) == 1

    def test_economy_bearish_bias_spy_rally_critical(self):
        """Bearish/Risk-Off bias but SPY rallied >5% → critical."""
        card = copy.deepcopy(self.SAMPLE_ECONOMY_CARD)
        card["marketBias"] = "Risk-Off"
        spy_ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        spy_ctx["meta"]["ticker"] = "SPY"
        spy_ctx["reference"]["yesterday_close"] = 240.0  # big rally from 240 to ~264
        report = validate_economy_data(card, etf_contexts={"SPY": spy_ctx}, trade_date="2026-02-23")
        critical = [i for i in report.issues if i.rule == "DATA_ECON_BIAS_CONTRADICTION"]
        assert len(critical) == 1

    def test_economy_no_etf_contexts_graceful(self):
        """No ETF contexts provided → validator skips gracefully."""
        report = validate_economy_data(
            self.SAMPLE_ECONOMY_CARD, etf_contexts=None, trade_date="2026-02-23",
        )
        assert isinstance(report, DataReport)
        assert report.passed  # no critical issues


# ==========================================
# SECTOR ROTATION AUDIT TESTS
# ==========================================

def _make_sector_ctx(yesterday_close: float, last_poc: float) -> dict:
    """Build a minimal ETF context card with controllable return."""
    return {
        "meta": {"ticker": "ETF", "date": "2026-02-23"},
        "status": "OK",
        "reference": {"yesterday_close": yesterday_close},
        "sessions": {
            "pre_market": {"status": "No Data"},
            "regular_hours": {
                "status": "Active",
                "high": last_poc + 1,
                "low": last_poc - 1,
                "volume_approx": 1_000_000,
                "value_migration": [
                    {"time": "16:00", "POC": last_poc, "nature": "Green", "range": f"{last_poc-1:.2f}-{last_poc+1:.2f}"},
                ],
            },
            "post_market": {"status": "No Data"},
        },
    }


class TestSectorRotationAudit:
    """Test the Sector Leadership / Rotation Audit validator."""

    def _build_etf_contexts(self, returns_map: dict[str, float]) -> dict:
        """returns_map: {ETF: desired_pct_return}.  Pins yesterday_close=100."""
        ctxs = {}
        for etf, pct in returns_map.items():
            # close_proxy = 100 * (1 + pct/100)
            ctxs[etf] = _make_sector_ctx(100.0, 100 * (1 + pct / 100))
        return ctxs

    def test_correct_rotation_no_issues(self):
        """Leading sector is actually the top performer → no issue."""
        returns = {"XLK": 2.0, "XLF": 1.0, "XLE": 0.5, "XLV": -0.2,
                   "XLI": -0.5, "XLC": -0.8, "XLP": -1.0, "XLU": -1.5, "SMH": 1.5}
        card = {
            "sectorRotation": {
                "leadingSectors": ["Technology"],
                "laggingSectors": ["Utilities"],
                "rotationAnalysis": "Risk-on rotation into growth.",
            },
        }
        report = validate_economy_data(card, etf_contexts=self._build_etf_contexts(returns))
        sector_issues = [i for i in report.issues if "SECTOR" in i.rule]
        assert len(sector_issues) == 0

    def test_false_leader_flagged(self):
        """Claims 'Energy' is leading but XLE is actually in the bottom third → critical."""
        returns = {"XLK": 2.0, "XLF": 1.5, "XLE": -2.0, "XLV": 0.0,
                   "XLI": 1.0, "XLC": 0.5, "XLP": -0.5, "XLU": -1.5, "SMH": 1.8}
        card = {
            "sectorRotation": {
                "leadingSectors": ["Energy"],
                "laggingSectors": [],
                "rotationAnalysis": "Energy leading on oil spike.",
            },
        }
        report = validate_economy_data(card, etf_contexts=self._build_etf_contexts(returns))
        issues = [i for i in report.issues if i.rule == "DATA_SECTOR_LEADER_FALSE"]
        assert len(issues) == 1
        assert "Energy" in issues[0].message
        assert issues[0].severity == "critical"

    def test_false_lagger_flagged(self):
        """Claims 'Technology' is lagging but XLK is actually the top → critical."""
        returns = {"XLK": 3.0, "XLF": 0.5, "XLE": -1.0, "XLV": 0.0,
                   "XLI": 0.2, "XLC": -0.3, "XLP": -0.5, "XLU": -1.0, "SMH": 1.0}
        card = {
            "sectorRotation": {
                "leadingSectors": [],
                "laggingSectors": ["Technology"],
                "rotationAnalysis": "Tech lagging on rotation.",
            },
        }
        report = validate_economy_data(card, etf_contexts=self._build_etf_contexts(returns))
        issues = [i for i in report.issues if i.rule == "DATA_SECTOR_LAGGER_FALSE"]
        assert len(issues) == 1
        assert "Technology" in issues[0].message

    def test_ticker_names_resolved(self):
        """Sector names given as ETF tickers (e.g. 'XLK') are resolved correctly."""
        returns = {"XLK": -2.0, "XLF": 1.0, "XLE": 0.5, "XLV": 0.0,
                   "XLI": 0.3, "XLC": 0.2, "XLP": -0.3, "XLU": -1.5, "SMH": 1.5}
        card = {
            "sectorRotation": {
                "leadingSectors": ["XLK"],  # ticker, not name — and it's bottom third
                "laggingSectors": [],
                "rotationAnalysis": "Tech leading.",
            },
        }
        report = validate_economy_data(card, etf_contexts=self._build_etf_contexts(returns))
        issues = [i for i in report.issues if i.rule == "DATA_SECTOR_LEADER_FALSE"]
        assert len(issues) == 1

    def test_not_enough_data_skips(self):
        """Fewer than 3 sector returns available → validator skips gracefully."""
        returns = {"XLK": 2.0, "XLF": -1.0}
        card = {
            "sectorRotation": {
                "leadingSectors": ["Financials"],
                "laggingSectors": [],
                "rotationAnalysis": "Financials led.",
            },
        }
        report = validate_economy_data(card, etf_contexts=self._build_etf_contexts(returns))
        sector_issues = [i for i in report.issues if "SECTOR" in i.rule]
        assert len(sector_issues) == 0  # not enough data to flag

    def test_unknown_sector_name_ignored(self):
        """Unrecognised sector name is silently skipped, no crash."""
        returns = {"XLK": 2.0, "XLF": 1.0, "XLE": 0.5, "XLV": -0.5,
                   "XLI": 0.2, "XLC": -0.3, "XLP": -1.0, "XLU": -1.5, "SMH": 0.8}
        card = {
            "sectorRotation": {
                "leadingSectors": ["MadeUpSector"],
                "laggingSectors": [],
                "rotationAnalysis": "N/A.",
            },
        }
        report = validate_economy_data(card, etf_contexts=self._build_etf_contexts(returns))
        sector_issues = [i for i in report.issues if "SECTOR" in i.rule]
        assert len(sector_issues) == 0

    def test_multiple_false_claims(self):
        """Multiple false leaders and laggers are each flagged independently."""
        returns = {"XLK": -2.0, "XLF": -1.5, "XLE": 2.0, "XLV": 1.5,
                   "XLI": 1.0, "XLC": 0.5, "XLP": -0.5, "XLU": -1.0, "SMH": 0.0}
        card = {
            "sectorRotation": {
                "leadingSectors": ["Technology", "Financials"],   # both bottom third
                "laggingSectors": ["Energy", "Health Care"],      # both top third
                "rotationAnalysis": "Defensive rotation.",
            },
        }
        report = validate_economy_data(card, etf_contexts=self._build_etf_contexts(returns))
        leader_issues = [i for i in report.issues if i.rule == "DATA_SECTOR_LEADER_FALSE"]
        lagger_issues = [i for i in report.issues if i.rule == "DATA_SECTOR_LAGGER_FALSE"]
        assert len(leader_issues) == 2
        assert len(lagger_issues) == 2


# ==========================================
# CROSS-INDEX SESSION ARC AUDIT TESTS
# ==========================================

class TestIndexSessionArcAudit:
    """Test the Cross-Index Consistency (Indices Audit) validator."""

    BASE_ECONOMY_CARD = {
        "marketNarrative": "Markets consolidated.",
        "marketBias": "Neutral",
        "keyActionLog": [{"date": "2026-02-23", "action": "Consolidation."}],
        "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": "Flat."},
        "indexAnalysis": {
            "pattern": "Indices consolidating.",
            "SPY": "SPY gapped up and printed higher lows throughout RTH. Defended $255.",
            "QQQ": "QQQ gapped down and failed to hold support.",
        },
        "interMarketAnalysis": {"bonds": "", "commodities": "", "currencies": "", "crypto": ""},
        "marketInternals": {"volatility": "VIX flat."},
    }

    def test_spy_gap_up_valid(self):
        """SPY narrative says 'gapped up' — context shows gap up → no issue."""
        spy_ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        spy_ctx["meta"]["ticker"] = "SPY"
        # prev close = 253.30, pre-market open range starts 254.80 → session_open approach
        # Ensure a clear gap up by setting gap_pct
        spy_ctx["sessions"]["pre_market"]["session_open"] = 256.0
        spy_ctx["sessions"]["pre_market"]["gap_pct"] = 1.07

        report = validate_economy_data(
            self.BASE_ECONOMY_CARD,
            etf_contexts={"SPY": spy_ctx},
            trade_date="2026-02-23",
        )
        gap_issues = [i for i in report.issues if i.rule == "DATA_GAP_MISMATCH" and "SPY" in i.field]
        assert len(gap_issues) == 0

    def test_spy_gap_up_false_flagged(self):
        """SPY narrative says 'gapped up' but price opened flat → critical."""
        spy_ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        spy_ctx["meta"]["ticker"] = "SPY"
        # Make prev close ABOVE open → no gap up
        spy_ctx["reference"]["yesterday_close"] = 260.0
        # Replace the pre-market migration to show open below prev close
        spy_ctx["sessions"]["pre_market"]["value_migration"] = [
            {"time": "09:00", "POC": 258.0, "nature": "Red", "range": "257.50-258.50"},
        ]
        spy_ctx["sessions"]["regular_hours"]["value_migration"] = [
            {"time": "14:30", "POC": 257.0, "nature": "Red", "range": "256.00-258.00"},
            {"time": "15:00", "POC": 257.5, "nature": "Red", "range": "256.50-258.50"},
            {"time": "15:30", "POC": 258.0, "nature": "Flat", "range": "257.00-259.00"},
        ]

        report = validate_economy_data(
            self.BASE_ECONOMY_CARD,
            etf_contexts={"SPY": spy_ctx},
            trade_date="2026-02-23",
        )
        gap_issues = [i for i in report.issues if i.rule == "DATA_GAP_MISMATCH" and "SPY" in i.field]
        assert len(gap_issues) == 1
        assert gap_issues[0].severity == "critical"

    def test_qqq_gap_down_valid(self):
        """QQQ narrative says 'gapped down' — context shows gap down → no issue."""
        qqq_ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        qqq_ctx["meta"]["ticker"] = "QQQ"
        qqq_ctx["reference"]["yesterday_close"] = 260.0
        qqq_ctx["sessions"]["pre_market"]["value_migration"] = [
            {"time": "09:00", "POC": 257.0, "nature": "Red", "range": "256.50-257.50"},
        ]

        report = validate_economy_data(
            self.BASE_ECONOMY_CARD,
            etf_contexts={"QQQ": qqq_ctx},
            trade_date="2026-02-23",
        )
        gap_issues = [i for i in report.issues if i.rule == "DATA_GAP_MISMATCH" and "QQQ" in i.field]
        assert len(gap_issues) == 0

    def test_spy_higher_lows_false_flagged(self):
        """SPY narrative claims 'higher lows' but migration blocks show descending lows → critical."""
        spy_ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        spy_ctx["meta"]["ticker"] = "SPY"
        # Craft descending lows in the migration blocks
        spy_ctx["sessions"]["regular_hours"]["value_migration"] = [
            {"time": "14:30", "POC": 260.0, "nature": "Red", "range": "258.00-261.00"},
            {"time": "15:00", "POC": 259.0, "nature": "Red", "range": "257.00-260.00"},
            {"time": "15:30", "POC": 258.0, "nature": "Red", "range": "256.00-259.00"},
            {"time": "16:00", "POC": 257.0, "nature": "Red", "range": "255.00-258.00"},
            {"time": "16:30", "POC": 256.0, "nature": "Red", "range": "254.00-257.00"},
        ]

        report = validate_economy_data(
            self.BASE_ECONOMY_CARD,
            etf_contexts={"SPY": spy_ctx},
            trade_date="2026-02-23",
        )
        hl_issues = [i for i in report.issues if i.rule == "DATA_HIGHER_LOWS_FALSE" and "SPY" in i.field]
        assert len(hl_issues) == 1
        assert hl_issues[0].severity == "critical"

    def test_spy_held_support_valid(self):
        """SPY narrative says 'Defended $255' and RTH low was above $255 → no issue."""
        spy_ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        spy_ctx["meta"]["ticker"] = "SPY"
        # RTH low = 255.54 which is above $255 (within tolerance)
        report = validate_economy_data(
            self.BASE_ECONOMY_CARD,
            etf_contexts={"SPY": spy_ctx},
            trade_date="2026-02-23",
        )
        support_issues = [i for i in report.issues if i.rule == "DATA_SUPPORT_BREACHED" and "SPY" in i.field]
        assert len(support_issues) == 0

    def test_no_index_analysis_skips_gracefully(self):
        """Card with empty indexAnalysis → no crash, no index arc issues."""
        card = copy.deepcopy(self.BASE_ECONOMY_CARD)
        card["indexAnalysis"] = {}
        spy_ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        spy_ctx["meta"]["ticker"] = "SPY"
        report = validate_economy_data(card, etf_contexts={"SPY": spy_ctx}, trade_date="2026-02-23")
        arc_issues = [i for i in report.issues if i.field.startswith("indexAnalysis")]
        assert len(arc_issues) == 0


# ==========================================
# INTER-MARKET BREADTH AUDIT TESTS
# ==========================================

class TestBreadthAudit:
    """Test the Inter-Market Breadth Audit validator."""

    def _build_index_contexts(self, spy_ret: float, qqq_ret: float, iwm_ret: float) -> dict:
        """Build minimal ETF contexts for the three major indices."""
        return {
            "SPY": _make_sector_ctx(100.0, 100 * (1 + spy_ret / 100)),
            "QQQ": _make_sector_ctx(100.0, 100 * (1 + qqq_ret / 100)),
            "IWM": _make_sector_ctx(100.0, 100 * (1 + iwm_ret / 100)),
        }

    def test_small_cap_strength_correct(self):
        """'Small caps showed relative strength' + IWM > SPY → no issue."""
        card = {
            "marketNarrative": "Small caps showed relative strength as breadth improved.",
            "indexAnalysis": {"pattern": "Broadening.", "SPY": "Flat.", "QQQ": "Flat."},
            "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": ""},
        }
        ctxs = self._build_index_contexts(spy_ret=0.5, qqq_ret=0.3, iwm_ret=1.2)
        report = validate_economy_data(card, etf_contexts=ctxs)
        breadth_issues = [i for i in report.issues if i.rule == "DATA_BREADTH_MISMATCH"]
        assert len(breadth_issues) == 0

    def test_small_cap_strength_false_flagged(self):
        """'Small caps showed relative strength' but IWM < SPY → critical."""
        card = {
            "marketNarrative": "Small caps showed relative strength today.",
            "indexAnalysis": {"pattern": "Narrowing.", "SPY": "Led.", "QQQ": "Flat."},
            "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": ""},
        }
        ctxs = self._build_index_contexts(spy_ret=1.5, qqq_ret=1.0, iwm_ret=0.2)
        report = validate_economy_data(card, etf_contexts=ctxs)
        breadth_issues = [i for i in report.issues if i.rule == "DATA_BREADTH_MISMATCH"]
        assert len(breadth_issues) == 1
        assert "IWM" in breadth_issues[0].message
        assert breadth_issues[0].severity == "critical"

    def test_narrow_breadth_correct(self):
        """'Breadth was narrow' + SPY > IWM → no issue."""
        card = {
            "marketNarrative": "Breadth was narrow as mega caps dominated.",
            "indexAnalysis": {"pattern": "", "SPY": "", "QQQ": ""},
            "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": ""},
        }
        ctxs = self._build_index_contexts(spy_ret=1.0, qqq_ret=1.2, iwm_ret=-0.3)
        report = validate_economy_data(card, etf_contexts=ctxs)
        breadth_issues = [i for i in report.issues if i.rule == "DATA_BREADTH_MISMATCH"]
        assert len(breadth_issues) == 0

    def test_narrow_breadth_false_flagged(self):
        """'Breadth was narrow' but IWM > SPY → critical."""
        card = {
            "marketNarrative": "Breadth was narrow with mega cap leadership.",
            "indexAnalysis": {"pattern": "", "SPY": "", "QQQ": ""},
            "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": ""},
        }
        ctxs = self._build_index_contexts(spy_ret=0.5, qqq_ret=0.3, iwm_ret=1.5)
        report = validate_economy_data(card, etf_contexts=ctxs)
        breadth_issues = [i for i in report.issues if i.rule == "DATA_BREADTH_MISMATCH"]
        assert len(breadth_issues) == 1

    def test_broad_breadth_correct(self):
        """'Breadth was broad' + IWM >= SPY → no issue."""
        card = {
            "marketNarrative": "Breadth was broad with small caps participating.",
            "indexAnalysis": {"pattern": "", "SPY": "", "QQQ": ""},
            "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": ""},
        }
        ctxs = self._build_index_contexts(spy_ret=0.8, qqq_ret=0.5, iwm_ret=1.0)
        report = validate_economy_data(card, etf_contexts=ctxs)
        breadth_issues = [i for i in report.issues if i.rule == "DATA_BREADTH_MISMATCH"]
        assert len(breadth_issues) == 0

    def test_qqq_outperform_correct(self):
        """'QQQ outperformed' + QQQ > SPY → no issue."""
        card = {
            "marketNarrative": "QQQ outperformed as tech led the rally.",
            "indexAnalysis": {"pattern": "", "SPY": "", "QQQ": ""},
            "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": ""},
        }
        ctxs = self._build_index_contexts(spy_ret=0.5, qqq_ret=1.5, iwm_ret=0.3)
        report = validate_economy_data(card, etf_contexts=ctxs)
        breadth_issues = [i for i in report.issues if i.rule == "DATA_BREADTH_MISMATCH"]
        assert len(breadth_issues) == 0

    def test_qqq_outperform_false_flagged(self):
        """'QQQ outperformed' but QQQ < SPY → critical."""
        card = {
            "marketNarrative": "QQQ outperformed the broader market.",
            "indexAnalysis": {"pattern": "", "SPY": "", "QQQ": ""},
            "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": ""},
        }
        ctxs = self._build_index_contexts(spy_ret=1.5, qqq_ret=0.3, iwm_ret=0.8)
        report = validate_economy_data(card, etf_contexts=ctxs)
        breadth_issues = [i for i in report.issues if i.rule == "DATA_BREADTH_MISMATCH"]
        assert len(breadth_issues) == 1
        assert "QQQ" in breadth_issues[0].message

    def test_tech_led_decline_correct(self):
        """'Tech led the decline' + QQQ worse than SPY → no issue."""
        card = {
            "todaysAction": "2026-02-23: Sell-off. Tech led the decline with QQQ underperforming.",
            "indexAnalysis": {"pattern": "", "SPY": "", "QQQ": ""},
            "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": ""},
        }
        ctxs = self._build_index_contexts(spy_ret=-1.0, qqq_ret=-2.5, iwm_ret=-0.5)
        report = validate_economy_data(card, etf_contexts=ctxs)
        # "tech led the decline" means SPY > QQQ (SPY performed better)
        breadth_issues = [i for i in report.issues if i.rule == "DATA_BREADTH_MISMATCH"]
        assert len(breadth_issues) == 0

    def test_no_breadth_claims_no_issues(self):
        """Card with no relative-strength claims → no breadth issues."""
        card = {
            "marketNarrative": "Markets were flat today.",
            "indexAnalysis": {"pattern": "Range-bound.", "SPY": "Flat.", "QQQ": "Flat."},
            "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": ""},
        }
        ctxs = self._build_index_contexts(spy_ret=0.1, qqq_ret=-0.2, iwm_ret=0.3)
        report = validate_economy_data(card, etf_contexts=ctxs)
        breadth_issues = [i for i in report.issues if i.rule == "DATA_BREADTH_MISMATCH"]
        assert len(breadth_issues) == 0

    def test_missing_index_context_skips_gracefully(self):
        """Breadth claim present but IWM context missing → no crash, no issue."""
        card = {
            "marketNarrative": "Small caps outperformed today.",
            "indexAnalysis": {"pattern": "", "SPY": "", "QQQ": ""},
            "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": ""},
        }
        # Only provide SPY, no IWM
        ctxs = {"SPY": _make_sector_ctx(100.0, 101.0)}
        report = validate_economy_data(card, etf_contexts=ctxs)
        breadth_issues = [i for i in report.issues if i.rule == "DATA_BREADTH_MISMATCH"]
        assert len(breadth_issues) == 0


# ==========================================
# EDGE CASES & INTEGRATION TESTS
# ==========================================

class TestEdgeCases:
    """Test edge cases and graceful degradation."""

    def test_empty_context(self):
        """Empty Impact Engine context → info issue, no crash."""
        report = validate_company_data(
            SAMPLE_COMPANY_CARD, {}, ticker="AAPL", trade_date="2026-02-23",
        )
        info_issues = [i for i in report.issues if i.rule == "DATA_NO_CONTEXT"]
        assert len(info_issues) == 1

    def test_none_context(self):
        """None Impact Engine context → info issue, no crash."""
        report = validate_company_data(
            SAMPLE_COMPANY_CARD, None, ticker="AAPL", trade_date="2026-02-23",
        )
        info_issues = [i for i in report.issues if i.rule == "DATA_NO_CONTEXT"]
        assert len(info_issues) == 1

    def test_no_data_context(self):
        """Context with status 'No Data' → info issue, no crash."""
        ctx = {"status": "No Data", "meta": {"ticker": "AAPL"}}
        report = validate_company_data(
            SAMPLE_COMPANY_CARD, ctx, ticker="AAPL", trade_date="2026-02-23",
        )
        info_issues = [i for i in report.issues if i.rule == "DATA_NO_CONTEXT"]
        assert len(info_issues) == 1

    def test_report_summary_format(self):
        """DataReport summary includes correct formatting."""
        report = DataReport(card_type="company", ticker="AAPL")
        assert "PASS" in report.summary()
        assert "COMPANY" in report.summary()
        assert "AAPL" in report.summary()

    def test_report_failed_with_critical(self):
        """DataReport with critical issue reports as failed."""
        report = DataReport(card_type="company", ticker="AAPL")
        report.issues.append(DataIssue(
            rule="TEST", severity="critical", field="test", message="fail",
        ))
        assert not report.passed
        assert report.critical_count == 1

    def test_report_all_issues_are_critical(self):
        """DataReport: all data issues are critical severity."""
        report = DataReport(card_type="company", ticker="AAPL")
        report.issues.append(DataIssue(
            rule="TEST", severity="critical", field="test", message="data issue",
        ))
        assert not report.passed
        assert report.critical_count == 1

    def test_full_validation_good_card_no_criticals(self):
        """A properly constructed card against matching data → no critical issues."""
        report = validate_company_data(
            SAMPLE_COMPANY_CARD, SAMPLE_CONTEXT_CARD,
            ticker="AAPL", trade_date="2026-02-23",
        )
        assert report.passed, f"Expected no critical issues but got:\n{report.details()}"

    def test_details_output(self):
        """DataReport details() produces readable multi-line output."""
        report = DataReport(card_type="company", ticker="TEST")
        report.issues.append(DataIssue(
            rule="DATA_TEST", severity="critical", field="test.field",
            message="Test critical message",
        ))
        details = report.details()
        assert "DATA_TEST" in details
        assert "🔴" in details
        assert "test.field" in details


# ==========================================
# TODAY'S ACTION DATE TESTS (ECONOMY)
# ==========================================

class TestTodaysActionDate:
    """Test the todaysAction date consistency validator for economy cards."""

    BASE_CARD = {
        "marketNarrative": "Markets consolidated.",
        "marketBias": "Neutral",
        "keyActionLog": [{"date": "2026-02-23", "action": "Consolidation."}],
        "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": ""},
        "indexAnalysis": {"pattern": "", "SPY": "", "QQQ": ""},
        "interMarketAnalysis": {"bonds": "", "commodities": "", "currencies": "", "crypto": ""},
        "marketInternals": {"volatility": ""},
    }

    def test_correct_date_in_todays_action(self):
        """todaysAction contains the trade date → no issue."""
        card = copy.deepcopy(self.BASE_CARD)
        card["todaysAction"] = "2026-02-23: Markets consolidated with SPY holding support."
        report = validate_economy_data(card, trade_date="2026-02-23")
        date_issues = [i for i in report.issues if i.rule == "DATA_TODAYS_ACTION_DATE"]
        assert len(date_issues) == 0

    def test_wrong_date_in_todays_action(self):
        """todaysAction has a wrong/stale date → critical."""
        card = copy.deepcopy(self.BASE_CARD)
        card["todaysAction"] = "2026-02-20: Markets rallied on earnings."
        report = validate_economy_data(card, trade_date="2026-02-23")
        date_issues = [i for i in report.issues if i.rule == "DATA_TODAYS_ACTION_DATE"]
        assert len(date_issues) == 1
        assert date_issues[0].severity == "critical"

    def test_missing_date_in_todays_action(self):
        """todaysAction has no date at all → critical."""
        card = copy.deepcopy(self.BASE_CARD)
        card["todaysAction"] = "Markets consolidated with no clear direction."
        report = validate_economy_data(card, trade_date="2026-02-23")
        date_issues = [i for i in report.issues if i.rule == "DATA_TODAYS_ACTION_DATE"]
        assert len(date_issues) == 1

    def test_empty_todays_action_skips(self):
        """Empty todaysAction → validator skips gracefully."""
        card = copy.deepcopy(self.BASE_CARD)
        card["todaysAction"] = ""
        report = validate_economy_data(card, trade_date="2026-02-23")
        date_issues = [i for i in report.issues if i.rule == "DATA_TODAYS_ACTION_DATE"]
        assert len(date_issues) == 0

    def test_no_trade_date_skips(self):
        """No trade_date provided → validator skips."""
        card = copy.deepcopy(self.BASE_CARD)
        card["todaysAction"] = "2026-02-20: Stale data."
        report = validate_economy_data(card, trade_date="")
        date_issues = [i for i in report.issues if i.rule == "DATA_TODAYS_ACTION_DATE"]
        assert len(date_issues) == 0


# ==========================================
# INTER-MARKET DIRECTION TESTS (ECONOMY)
# ==========================================

class TestIntermarketDirection:
    """Test the inter-market direction claims validator for economy cards."""

    BASE_CARD = {
        "marketNarrative": "Risk-on tone.",
        "marketBias": "Neutral",
        "keyActionLog": [{"date": "2026-02-23", "action": "Consolidation."}],
        "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": ""},
        "indexAnalysis": {"pattern": "", "SPY": "", "QQQ": ""},
        "interMarketAnalysis": {
            "bonds": "",
            "commodities": "",
            "currencies": "",
            "crypto": "",
        },
        "marketInternals": {"volatility": ""},
    }

    def test_bonds_rallied_and_tlt_up_no_issue(self):
        """Claims 'TLT rallied' and TLT actually up → no issue."""
        card = copy.deepcopy(self.BASE_CARD)
        card["interMarketAnalysis"]["bonds"] = "TLT rallied 0.5% as a safety bid emerged."
        # TLT: 100 → 101 = +1%
        ctxs = {"TLT": _make_sector_ctx(100.0, 101.0)}
        report = validate_economy_data(card, etf_contexts=ctxs)
        dir_issues = [i for i in report.issues if i.rule == "DATA_INTERMARKET_DIRECTION"]
        assert len(dir_issues) == 0

    def test_bonds_rallied_but_tlt_dropped_flagged(self):
        """Claims 'bonds rallied' but TLT actually dropped → critical."""
        card = copy.deepcopy(self.BASE_CARD)
        card["interMarketAnalysis"]["bonds"] = "Bonds rallied with TLT gaining on the session."
        # TLT: 100 → 98.5 = -1.5%
        ctxs = {"TLT": _make_sector_ctx(100.0, 98.5)}
        report = validate_economy_data(card, etf_contexts=ctxs)
        dir_issues = [i for i in report.issues if i.rule == "DATA_INTERMARKET_DIRECTION"]
        assert len(dir_issues) == 1
        assert dir_issues[0].severity == "critical"
        assert "bonds" in dir_issues[0].field

    def test_crypto_fell_and_btc_down_no_issue(self):
        """Claims 'BTC dropped' and BTC actually down → no issue."""
        card = copy.deepcopy(self.BASE_CARD)
        card["interMarketAnalysis"]["crypto"] = "Bitcoin dropped 3% as risk appetite faded."
        # BTCUSDT: 100 → 97 = -3%
        ctxs = {"BTCUSDT": _make_sector_ctx(100.0, 97.0)}
        report = validate_economy_data(card, etf_contexts=ctxs)
        dir_issues = [i for i in report.issues if i.rule == "DATA_INTERMARKET_DIRECTION"]
        assert len(dir_issues) == 0

    def test_crypto_surged_but_btc_down_flagged(self):
        """Claims 'BTC surged' but BTC actually fell → critical."""
        card = copy.deepcopy(self.BASE_CARD)
        card["interMarketAnalysis"]["crypto"] = "Bitcoin surged on strong buying."
        # BTCUSDT: 100 → 98 = -2%
        ctxs = {"BTCUSDT": _make_sector_ctx(100.0, 98.0)}
        report = validate_economy_data(card, etf_contexts=ctxs)
        dir_issues = [i for i in report.issues if i.rule == "DATA_INTERMARKET_DIRECTION"]
        assert len(dir_issues) == 1
        assert "crypto" in dir_issues[0].field

    def test_oil_flat_no_flag(self):
        """Claims 'oil flat' and return is tiny → no issue (flat is ambiguous)."""
        card = copy.deepcopy(self.BASE_CARD)
        card["interMarketAnalysis"]["commodities"] = "Oil was flat, trading in a tight range."
        # CL=F: 100 → 100.1 = +0.1% (below 0.3% threshold)
        ctxs = {"CL=F": _make_sector_ctx(100.0, 100.1)}
        report = validate_economy_data(card, etf_contexts=ctxs)
        dir_issues = [i for i in report.issues if i.rule == "DATA_INTERMARKET_DIRECTION"]
        assert len(dir_issues) == 0

    def test_commodities_rose_but_oil_flat_no_flag(self):
        """Claims 'commodities rose' but CL=F moved only 0.1% → skip (below threshold)."""
        card = copy.deepcopy(self.BASE_CARD)
        card["interMarketAnalysis"]["commodities"] = "Commodities rose slightly."
        # CL=F: 100 → 99.9 = -0.1% (below 0.3% threshold, so skip)
        ctxs = {"CL=F": _make_sector_ctx(100.0, 99.9)}
        report = validate_economy_data(card, etf_contexts=ctxs)
        dir_issues = [i for i in report.issues if i.rule == "DATA_INTERMARKET_DIRECTION"]
        assert len(dir_issues) == 0

    def test_currencies_dollar_strengthened_uup_up_no_issue(self):
        """Claims 'Dollar strengthened' and UUP is up → no issue."""
        card = copy.deepcopy(self.BASE_CARD)
        card["interMarketAnalysis"]["currencies"] = "The dollar rose as UUP gained 0.5%."
        # UUP: 100 → 100.8 = +0.8%
        ctxs = {"UUP": _make_sector_ctx(100.0, 100.8)}
        report = validate_economy_data(card, etf_contexts=ctxs)
        dir_issues = [i for i in report.issues if i.rule == "DATA_INTERMARKET_DIRECTION"]
        assert len(dir_issues) == 0

    def test_currencies_dollar_weakened_but_uup_up_flagged(self):
        """Claims 'Dollar weakened' but UUP is up → critical."""
        card = copy.deepcopy(self.BASE_CARD)
        card["interMarketAnalysis"]["currencies"] = "The dollar weakened significantly."
        # UUP: 100 → 101 = +1%
        ctxs = {"UUP": _make_sector_ctx(100.0, 101.0)}
        report = validate_economy_data(card, etf_contexts=ctxs)
        dir_issues = [i for i in report.issues if i.rule == "DATA_INTERMARKET_DIRECTION"]
        assert len(dir_issues) == 1
        assert "currencies" in dir_issues[0].field

    def test_no_intermarket_section_skips(self):
        """No interMarketAnalysis key → validator skips gracefully."""
        card = copy.deepcopy(self.BASE_CARD)
        del card["interMarketAnalysis"]
        ctxs = {"TLT": _make_sector_ctx(100.0, 101.0)}
        report = validate_economy_data(card, etf_contexts=ctxs)
        dir_issues = [i for i in report.issues if i.rule == "DATA_INTERMARKET_DIRECTION"]
        assert len(dir_issues) == 0

    def test_missing_etf_context_skips(self):
        """Directional claim present but ETF context missing → no crash."""
        card = copy.deepcopy(self.BASE_CARD)
        card["interMarketAnalysis"]["bonds"] = "TLT rallied significantly."
        report = validate_economy_data(card, etf_contexts={})
        dir_issues = [i for i in report.issues if i.rule == "DATA_INTERMARKET_DIRECTION"]
        assert len(dir_issues) == 0

    def test_detect_direction_up(self):
        """Helper: detects 'up' direction from bullish language."""
        assert _detect_direction("TLT rallied 0.5% on the session.") == "up"
        assert _detect_direction("Bitcoin surged higher on inflows.") == "up"

    def test_detect_direction_down(self):
        """Helper: detects 'down' direction from bearish language."""
        assert _detect_direction("Oil dropped sharply on demand fears.") == "down"
        assert _detect_direction("TLT fell on the session.") == "down"

    def test_detect_direction_ambiguous(self):
        """Helper: ambiguous text returns None."""
        assert _detect_direction("Markets were range bound and quiet.") is None


# ==========================================
# QUOTED RETURN MAGNITUDE TESTS (ECONOMY)
# ==========================================

class TestReturnMagnitude:
    """Test the quoted return magnitude validator for economy cards."""

    BASE_CARD = {
        "marketNarrative": "",
        "marketBias": "Neutral",
        "keyActionLog": [{"date": "2026-02-23", "action": "Consolidation."}],
        "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": ""},
        "indexAnalysis": {"pattern": "", "SPY": "", "QQQ": ""},
        "interMarketAnalysis": {"bonds": "", "commodities": "", "currencies": "", "crypto": ""},
        "marketInternals": {"volatility": ""},
    }

    def test_accurate_return_quote_no_issue(self):
        """Quoted 'SPY +1.0%' and actual is +0.95% → within tolerance, no issue."""
        card = copy.deepcopy(self.BASE_CARD)
        card["todaysAction"] = "2026-02-23: Risk-On. SPY +1.0% on broad participation."
        # SPY: 100 → 100.95 = +0.95% (diff = 0.05pp, well within 0.80pp tolerance)
        ctxs = {"SPY": _make_sector_ctx(100.0, 100.95)}
        report = validate_economy_data(card, etf_contexts=ctxs, trade_date="2026-02-23")
        mag_issues = [i for i in report.issues if i.rule == "DATA_RETURN_MAGNITUDE"]
        assert len(mag_issues) == 0

    def test_inaccurate_return_quote_flagged(self):
        """Quoted 'SPY +2.5%' but actual is +0.5% → off by 2.0pp, critical."""
        card = copy.deepcopy(self.BASE_CARD)
        card["marketNarrative"] = "SPY +2.5% on massive buying."
        # SPY: 100 → 100.5 = +0.5% (diff = 2.0pp, exceeds tolerance)
        ctxs = {"SPY": _make_sector_ctx(100.0, 100.5)}
        report = validate_economy_data(card, etf_contexts=ctxs)
        mag_issues = [i for i in report.issues if i.rule == "DATA_RETURN_MAGNITUDE"]
        assert len(mag_issues) == 1
        assert mag_issues[0].severity == "critical"
        assert "SPY" in mag_issues[0].message

    def test_negative_return_quote_accurate(self):
        """Quoted 'TLT -0.5%' and actual is -0.6% → within tolerance."""
        card = copy.deepcopy(self.BASE_CARD)
        card["interMarketAnalysis"]["bonds"] = "TLT -0.5% as yields ticked higher."
        # TLT: 100 → 99.4 = -0.6% (diff = 0.1pp)
        ctxs = {"TLT": _make_sector_ctx(100.0, 99.4)}
        report = validate_economy_data(card, etf_contexts=ctxs)
        mag_issues = [i for i in report.issues if i.rule == "DATA_RETURN_MAGNITUDE"]
        assert len(mag_issues) == 0

    def test_negative_return_quote_inaccurate(self):
        """Quoted 'QQQ -0.3%' but actual is +1.5% → sign and magnitude wrong."""
        card = copy.deepcopy(self.BASE_CARD)
        card["indexAnalysis"]["QQQ"] = "QQQ -0.3% as tech lagged slightly."
        # QQQ: 100 → 101.5 = +1.5% (diff = 1.8pp)
        ctxs = {"QQQ": _make_sector_ctx(100.0, 101.5)}
        report = validate_economy_data(card, etf_contexts=ctxs)
        mag_issues = [i for i in report.issues if i.rule == "DATA_RETURN_MAGNITUDE"]
        assert len(mag_issues) == 1

    def test_multiple_return_quotes_multiple_flags(self):
        """Multiple inaccurate quotes → each flagged independently."""
        card = copy.deepcopy(self.BASE_CARD)
        card["todaysAction"] = "2026-02-23: SPY +3.0% and QQQ +4.0% on massive rally."
        # SPY: 100 → 100.5 = +0.5%, QQQ: 100 → 101.0 = +1.0%
        ctxs = {
            "SPY": _make_sector_ctx(100.0, 100.5),
            "QQQ": _make_sector_ctx(100.0, 101.0),
        }
        report = validate_economy_data(card, etf_contexts=ctxs, trade_date="2026-02-23")
        mag_issues = [i for i in report.issues if i.rule == "DATA_RETURN_MAGNITUDE"]
        assert len(mag_issues) == 2

    def test_no_return_quotes_no_issues(self):
        """No percentage figures in narrative → no issues."""
        card = copy.deepcopy(self.BASE_CARD)
        card["marketNarrative"] = "Markets consolidated in a tight range."
        ctxs = {"SPY": _make_sector_ctx(100.0, 101.0)}
        report = validate_economy_data(card, etf_contexts=ctxs)
        mag_issues = [i for i in report.issues if i.rule == "DATA_RETURN_MAGNITUDE"]
        assert len(mag_issues) == 0

    def test_unknown_ticker_quote_skipped(self):
        """Quoted return for a ticker not in etf_contexts → silently skipped."""
        card = copy.deepcopy(self.BASE_CARD)
        card["marketNarrative"] = "ARKK +5.0% as innovation names rallied."
        ctxs = {"SPY": _make_sector_ctx(100.0, 101.0)}
        report = validate_economy_data(card, etf_contexts=ctxs)
        mag_issues = [i for i in report.issues if i.rule == "DATA_RETURN_MAGNITUDE"]
        assert len(mag_issues) == 0

    def test_sector_return_in_rotation_analysis(self):
        """Quoted sector return in rotationAnalysis → validated."""
        card = copy.deepcopy(self.BASE_CARD)
        card["sectorRotation"]["rotationAnalysis"] = "XLK +3.0% led the day."
        # XLK: 100 → 100.5 = +0.5% (diff = 2.5pp, exceeds tolerance)
        ctxs = {"XLK": _make_sector_ctx(100.0, 100.5)}
        report = validate_economy_data(card, etf_contexts=ctxs)
        mag_issues = [i for i in report.issues if i.rule == "DATA_RETURN_MAGNITUDE"]
        assert len(mag_issues) == 1


# ==========================================
# MULTI-INDEX BIAS CONSISTENCY TESTS (ECONOMY)
# ==========================================

class TestMultiIndexBias:
    """Test the multi-index bias consistency validator for economy cards."""

    def _build_index_contexts(self, spy_ret: float, qqq_ret: float, iwm_ret: float) -> dict:
        return {
            "SPY": _make_sector_ctx(100.0, 100 * (1 + spy_ret / 100)),
            "QQQ": _make_sector_ctx(100.0, 100 * (1 + qqq_ret / 100)),
            "IWM": _make_sector_ctx(100.0, 100 * (1 + iwm_ret / 100)),
        }

    def test_bullish_all_up_no_issue(self):
        """Bullish bias and all 3 indices positive → no issue."""
        card = {
            "marketBias": "Bullish",
            "keyActionLog": [{"date": "2026-02-23", "action": "Risk-on."}],
        }
        ctxs = self._build_index_contexts(spy_ret=1.5, qqq_ret=2.0, iwm_ret=1.0)
        report = validate_economy_data(card, etf_contexts=ctxs, trade_date="2026-02-23")
        multi_issues = [i for i in report.issues if i.rule == "DATA_ECON_BIAS_MULTI_INDEX"]
        assert len(multi_issues) == 0

    def test_bullish_majority_down_flagged(self):
        """Bullish bias but 2/3 indices dropped > 2% → critical."""
        card = {
            "marketBias": "Risk-On",
            "keyActionLog": [{"date": "2026-02-23", "action": "Risk-on."}],
        }
        ctxs = self._build_index_contexts(spy_ret=-3.0, qqq_ret=-2.5, iwm_ret=0.5)
        report = validate_economy_data(card, etf_contexts=ctxs, trade_date="2026-02-23")
        multi_issues = [i for i in report.issues if i.rule == "DATA_ECON_BIAS_MULTI_INDEX"]
        assert len(multi_issues) == 1
        assert multi_issues[0].severity == "critical"
        assert "SPY" in multi_issues[0].message
        assert "QQQ" in multi_issues[0].message

    def test_bearish_majority_up_flagged(self):
        """Bearish bias but 2/3 indices rallied > 2% → critical."""
        card = {
            "marketBias": "Risk-Off",
            "keyActionLog": [{"date": "2026-02-23", "action": "Risk-off."}],
        }
        ctxs = self._build_index_contexts(spy_ret=3.0, qqq_ret=2.5, iwm_ret=-0.5)
        report = validate_economy_data(card, etf_contexts=ctxs, trade_date="2026-02-23")
        multi_issues = [i for i in report.issues if i.rule == "DATA_ECON_BIAS_MULTI_INDEX"]
        assert len(multi_issues) == 1
        assert "SPY" in multi_issues[0].message
        assert "QQQ" in multi_issues[0].message

    def test_bullish_one_down_no_flag(self):
        """Bullish bias but only 1/3 indices down → not majority, no flag."""
        card = {
            "marketBias": "Bullish",
            "keyActionLog": [{"date": "2026-02-23", "action": "Risk-on."}],
        }
        ctxs = self._build_index_contexts(spy_ret=1.5, qqq_ret=-3.0, iwm_ret=1.0)
        report = validate_economy_data(card, etf_contexts=ctxs, trade_date="2026-02-23")
        multi_issues = [i for i in report.issues if i.rule == "DATA_ECON_BIAS_MULTI_INDEX"]
        assert len(multi_issues) == 0

    def test_neutral_bias_no_flag(self):
        """Neutral bias → multi-index check skips."""
        card = {
            "marketBias": "Neutral (Bullish Lean)",
            "keyActionLog": [{"date": "2026-02-23", "action": "Wait."}],
        }
        ctxs = self._build_index_contexts(spy_ret=-5.0, qqq_ret=-4.0, iwm_ret=-6.0)
        report = validate_economy_data(card, etf_contexts=ctxs, trade_date="2026-02-23")
        multi_issues = [i for i in report.issues if i.rule == "DATA_ECON_BIAS_MULTI_INDEX"]
        assert len(multi_issues) == 0

    def test_only_one_index_available_skips(self):
        """Only one index context available → can't do majority check, skips."""
        card = {
            "marketBias": "Bullish",
            "keyActionLog": [{"date": "2026-02-23", "action": "Risk-on."}],
        }
        ctxs = {"SPY": _make_sector_ctx(100.0, 95.0)}  # only SPY, -5%
        report = validate_economy_data(card, etf_contexts=ctxs, trade_date="2026-02-23")
        multi_issues = [i for i in report.issues if i.rule == "DATA_ECON_BIAS_MULTI_INDEX"]
        assert len(multi_issues) == 0

    def test_all_three_contradict_flagged(self):
        """Bullish but all 3 indices massively down → flagged (3/3)."""
        card = {
            "marketBias": "Bullish",
            "keyActionLog": [{"date": "2026-02-23", "action": "Risk-on."}],
        }
        ctxs = self._build_index_contexts(spy_ret=-4.0, qqq_ret=-5.0, iwm_ret=-3.0)
        report = validate_economy_data(card, etf_contexts=ctxs, trade_date="2026-02-23")
        multi_issues = [i for i in report.issues if i.rule == "DATA_ECON_BIAS_MULTI_INDEX"]
        assert len(multi_issues) == 1
        assert "3/3" in multi_issues[0].message


# ==========================================
# IWM INDEX SESSION ARC TESTS (ECONOMY)
# ==========================================

class TestIWMIndexSessionArcs:
    """Test that IWM session arcs are now validated (in addition to SPY/QQQ)."""

    def test_iwm_gap_up_false_flagged(self):
        """IWM narrative says 'gapped up' but data shows no gap → critical."""
        card = {
            "marketNarrative": "Broad rally.",
            "marketBias": "Neutral",
            "keyActionLog": [{"date": "2026-02-23", "action": "Consolidation."}],
            "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": ""},
            "indexAnalysis": {
                "pattern": "Broad rally.",
                "SPY": "SPY held support.",
                "QQQ": "QQQ consolidated.",
                "IWM": "IWM gapped up and printed higher lows throughout RTH.",
            },
            "interMarketAnalysis": {"bonds": "", "commodities": "", "currencies": "", "crypto": ""},
            "marketInternals": {"volatility": ""},
        }
        iwm_ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        iwm_ctx["meta"]["ticker"] = "IWM"
        # prev close above the open → no gap up
        iwm_ctx["reference"]["yesterday_close"] = 270.0
        iwm_ctx["sessions"]["pre_market"]["value_migration"] = [
            {"time": "09:00", "POC": 265.0, "nature": "Red", "range": "264.00-266.00"},
        ]
        iwm_ctx["sessions"]["regular_hours"]["value_migration"] = [
            {"time": "14:30", "POC": 264.0, "nature": "Red", "range": "263.00-265.00"},
            {"time": "15:00", "POC": 263.5, "nature": "Red", "range": "262.50-264.50"},
            {"time": "15:30", "POC": 263.0, "nature": "Red", "range": "262.00-264.00"},
        ]

        report = validate_economy_data(
            card,
            etf_contexts={"IWM": iwm_ctx},
            trade_date="2026-02-23",
        )
        gap_issues = [i for i in report.issues if i.rule == "DATA_GAP_MISMATCH" and "IWM" in i.field]
        assert len(gap_issues) == 1
        assert gap_issues[0].severity == "critical"

    def test_iwm_higher_lows_false_flagged(self):
        """IWM narrative claims 'higher lows' but lows descend → critical."""
        card = {
            "marketNarrative": "Quiet day.",
            "marketBias": "Neutral",
            "keyActionLog": [{"date": "2026-02-23", "action": "Range."}],
            "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": ""},
            "indexAnalysis": {
                "pattern": "Range.",
                "SPY": "",
                "QQQ": "",
                "IWM": "IWM printed higher lows and defended $220 support.",
            },
            "interMarketAnalysis": {"bonds": "", "commodities": "", "currencies": "", "crypto": ""},
            "marketInternals": {"volatility": ""},
        }
        iwm_ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        iwm_ctx["meta"]["ticker"] = "IWM"
        iwm_ctx["sessions"]["regular_hours"]["value_migration"] = [
            {"time": "14:30", "POC": 225.0, "nature": "Red", "range": "224.00-226.00"},
            {"time": "15:00", "POC": 224.0, "nature": "Red", "range": "222.00-225.00"},
            {"time": "15:30", "POC": 223.0, "nature": "Red", "range": "220.00-224.00"},
            {"time": "16:00", "POC": 222.0, "nature": "Red", "range": "218.00-223.00"},
            {"time": "16:30", "POC": 221.0, "nature": "Red", "range": "216.00-222.00"},
        ]

        report = validate_economy_data(
            card,
            etf_contexts={"IWM": iwm_ctx},
            trade_date="2026-02-23",
        )
        hl_issues = [i for i in report.issues if i.rule == "DATA_HIGHER_LOWS_FALSE" and "IWM" in i.field]
        assert len(hl_issues) == 1

    def test_iwm_not_in_index_analysis_skipped(self):
        """No IWM key in indexAnalysis → no IWM validation attempted."""
        card = {
            "marketNarrative": "Markets flat.",
            "marketBias": "Neutral",
            "keyActionLog": [{"date": "2026-02-23", "action": "Range."}],
            "sectorRotation": {"leadingSectors": [], "laggingSectors": [], "rotationAnalysis": ""},
            "indexAnalysis": {"pattern": "Range-bound.", "SPY": "SPY flat.", "QQQ": "QQQ flat."},
            "interMarketAnalysis": {"bonds": "", "commodities": "", "currencies": "", "crypto": ""},
            "marketInternals": {"volatility": ""},
        }
        iwm_ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        iwm_ctx["meta"]["ticker"] = "IWM"
        report = validate_economy_data(
            card, etf_contexts={"IWM": iwm_ctx}, trade_date="2026-02-23",
        )
        iwm_arc_issues = [i for i in report.issues if "IWM" in i.field and i.rule.startswith("DATA_")]
        assert len(iwm_arc_issues) == 0


# ==========================================
# FULL INTEGRATION TEST: ECONOMY CARD
# ==========================================

class TestEconomyIntegration:
    """End-to-end integration tests combining multiple economy validators."""

    def _build_full_contexts(self) -> dict:
        """Build a comprehensive etf_contexts with all 20 assets."""
        ctxs = {}
        # Major indices
        for t, ret in [("SPY", 1.2), ("QQQ", 0.8), ("IWM", 1.5), ("DIA", 0.9)]:
            ctxs[t] = _make_sector_ctx(100.0, 100 * (1 + ret / 100))
        # Sectors
        for t, ret in [("XLK", 1.5), ("XLF", 0.5), ("XLE", -0.8), ("XLV", 0.2),
                       ("XLI", 1.0), ("XLC", 0.3), ("XLP", -0.3), ("XLU", -1.0), ("SMH", 2.0)]:
            ctxs[t] = _make_sector_ctx(100.0, 100 * (1 + ret / 100))
        # Inter-market
        ctxs["TLT"] = _make_sector_ctx(100.0, 99.5)  # -0.5%
        ctxs["BTCUSDT"] = _make_sector_ctx(100.0, 101.8)  # +1.8%
        ctxs["CL=F"] = _make_sector_ctx(100.0, 100.1)  # +0.1% (flat)
        ctxs["UUP"] = _make_sector_ctx(100.0, 99.8)  # -0.2%
        return ctxs

    def test_good_economy_card_passes(self):
        """A well-constructed economy card matching all data → no critical issues."""
        card = {
            "marketNarrative": "Risk-on tone with broad participation. SPY +1.2% and IWM +1.5%.",
            "marketBias": "Bullish",
            "keyActionLog": [{"date": "2026-02-23", "action": "2026-02-23: Risk-on rally."}],
            "keyEconomicEvents": {"last_24h": "PCE in line.", "next_24h": "ISM PMI."},
            "sectorRotation": {
                "leadingSectors": ["Semiconductors", "Technology"],
                "laggingSectors": ["Utilities", "Energy"],
                "rotationAnalysis": "Risk-on rotation into growth. SMH +2.0% led.",
            },
            "indexAnalysis": {
                "pattern": "Broad participation with small caps leading.",
                "SPY": "SPY held support and closed near highs.",
                "QQQ": "QQQ lagged slightly but held gains.",
            },
            "interMarketAnalysis": {
                "bonds": "TLT fell as yields ticked higher.",
                "commodities": "Oil flat, trading in a tight range.",
                "currencies": "Dollar stable with UUP flat.",
                "crypto": "Bitcoin rallied 1.8% as risk appetite returned.",
            },
            "marketInternals": {"volatility": "VIX at 14.2, signaling complacency."},
            "todaysAction": "2026-02-23: Risk-On Rally. Broad participation with SMH leading at +2.0%. Small caps showed relative strength with IWM outperforming SPY.",
        }
        ctxs = self._build_full_contexts()
        report = validate_economy_data(card, etf_contexts=ctxs, trade_date="2026-02-23")
        assert report.passed, f"Expected PASS but got:\\n{report.details()}"

    def test_bad_economy_card_catches_everything(self):
        """An economy card with multiple hallucinations → multiple criticals."""
        card = {
            "marketNarrative": "Risk-off selloff. SPY -3.0% as markets crashed.",
            "marketBias": "Bullish",  # CONTRADICTION: bullish on a crash?
            "keyActionLog": [{"date": "2026-02-20", "action": "2026-02-20: Stale."}],  # WRONG DATE
            "keyEconomicEvents": {"last_24h": "PCE.", "next_24h": "ISM."},
            "sectorRotation": {
                "leadingSectors": ["Utilities"],  # WRONG: XLU is bottom
                "laggingSectors": ["Semiconductors"],  # WRONG: SMH is top
                "rotationAnalysis": "Defensive rotation.",
            },
            "indexAnalysis": {
                "pattern": "Sell-off.",
                "SPY": "SPY gapped down and sold off.",  # WRONG: SPY +1.2%
                "QQQ": "QQQ printed higher lows.",  # Needs checking
            },
            "interMarketAnalysis": {
                "bonds": "TLT surged as safety bid emerged.",  # WRONG: TLT -0.5%
                "commodities": "Oil flat.",
                "currencies": "Dollar stable.",
                "crypto": "Bitcoin dropped sharply.",  # WRONG: BTC +1.8%
            },
            "marketInternals": {"volatility": "VIX spiked."},
            "todaysAction": "2026-02-20: Broad selloff.",  # WRONG DATE
        }
        ctxs = self._build_full_contexts()
        report = validate_economy_data(card, etf_contexts=ctxs, trade_date="2026-02-23")
        assert not report.passed, f"Expected FAIL but got:\\n{report.details()}"

        # Check specific issue types are caught
        rules_found = {i.rule for i in report.issues}
        assert "DATA_LOG_DATE_STALE" in rules_found
        assert "DATA_TODAYS_ACTION_DATE" in rules_found
        assert "DATA_SECTOR_LEADER_FALSE" in rules_found or "DATA_SECTOR_LAGGER_FALSE" in rules_found
        assert "DATA_INTERMARKET_DIRECTION" in rules_found
        assert "DATA_RETURN_MAGNITUDE" in rules_found
