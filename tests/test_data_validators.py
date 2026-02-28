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
    _extract_bias,
    _get_rth_return,
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
        "yesterday_close": 255.30,
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
        "valuation": "28x forward P/E",
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

    def test_extract_bias_bullish(self):
        assert _extract_bias("Trend_Bias: Bullish (Story_Confidence: High)") == "Bullish"

    def test_extract_bias_bearish(self):
        assert _extract_bias("Trend_Bias: Bearish (Story_Confidence: Low)") == "Bearish"

    def test_extract_bias_neutral(self):
        assert _extract_bias("Trend_Bias: Neutral (Story_Confidence: Medium)") == "Neutral"

    def test_extract_bias_missing(self):
        assert _extract_bias("Some random text") is None

    def test_extract_bias_underscore_variant(self):
        assert _extract_bias("Trend Bias: Bullish") == "Bullish"

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
    """Test directional / bias claim validators."""

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

    def test_bullish_bias_on_mild_down_day_warning(self):
        """Bullish bias on a day that dropped 2-5% → warning."""
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        # ~275 → ~264 ≈ -4%
        ctx["reference"]["yesterday_close"] = 275.0
        report = validate_company_data(
            SAMPLE_COMPANY_CARD, ctx, ticker="AAPL", trade_date="2026-02-23",
        )
        warnings = [i for i in report.issues if i.rule == "DATA_BIAS_MISMATCH"]
        assert len(warnings) == 1
        assert warnings[0].severity == "warning"

    def test_bearish_bias_on_big_up_day_critical(self):
        """Bearish bias on a huge rally → critical."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["confidence"] = "Trend_Bias: Bearish (Story_Confidence: High) - Reasoning: Breakdown below support."
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
        card["confidence"] = "Trend_Bias: Bearish (Story_Confidence: Medium)"
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        ctx["reference"]["yesterday_close"] = 257.0  # ~257 → ~264 ≈ +2.7%
        report = validate_company_data(card, ctx, ticker="AAPL", trade_date="2026-02-23")
        warnings = [i for i in report.issues if i.rule == "DATA_BIAS_MISMATCH"]
        assert len(warnings) == 1

    def test_neutral_bias_no_contradiction(self):
        """Neutral bias should never trigger bias contradictions."""
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["confidence"] = "Trend_Bias: Neutral (Story_Confidence: Medium)"
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        ctx["reference"]["yesterday_close"] = 290.0  # big drop
        report = validate_company_data(card, ctx, ticker="AAPL", trade_date="2026-02-23")
        bias_issues = [i for i in report.issues if "BIAS" in i.rule]
        assert len(bias_issues) == 0

    def test_price_trend_bullish_language_matches(self):
        """Bullish priceTrend on a day where POC migrated higher → no issue."""
        report = validate_company_data(
            SAMPLE_COMPANY_CARD, SAMPLE_CONTEXT_CARD,
            ticker="AAPL", trade_date="2026-02-23",
        )
        trend_issues = [i for i in report.issues if i.rule == "DATA_TREND_MISMATCH"]
        assert len(trend_issues) == 0

    def test_price_trend_bullish_on_declining_poc_warns(self):
        """Bullish priceTrend when POCs were actually declining → warning."""
        ctx = copy.deepcopy(SAMPLE_CONTEXT_CARD)
        # Make POCs decline
        ctx["sessions"]["regular_hours"]["value_migration"] = [
            {"time": "14:30", "POC": 265.00, "nature": "Red", "range": "264.00-266.00"},
            {"time": "15:00", "POC": 264.00, "nature": "Red", "range": "263.00-265.00"},
            {"time": "15:30", "POC": 263.00, "nature": "Red", "range": "262.00-264.00"},
            {"time": "16:00", "POC": 262.00, "nature": "Red", "range": "261.00-263.00"},
            {"time": "16:30", "POC": 260.50, "nature": "Red", "range": "259.00-261.00"},
            {"time": "17:00", "POC": 259.00, "nature": "Red", "range": "258.00-260.00"},
        ]
        card = copy.deepcopy(SAMPLE_COMPANY_CARD)
        card["basicContext"]["priceTrend"] = "Strong uptrend with breakout continuation."
        report = validate_company_data(card, ctx, ticker="AAPL", trade_date="2026-02-23")
        trend_issues = [i for i in report.issues if i.rule == "DATA_TREND_MISMATCH"]
        assert len(trend_issues) == 1
        assert "bullish" in trend_issues[0].message.lower()


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

    def test_report_passed_with_warnings_only(self):
        """DataReport with only warnings still passes."""
        report = DataReport(card_type="company", ticker="AAPL")
        report.issues.append(DataIssue(
            rule="TEST", severity="warning", field="test", message="warn",
        ))
        assert report.passed
        assert report.warning_count == 1

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
            rule="DATA_TEST", severity="warning", field="test.field",
            message="Test warning message",
        ))
        details = report.details()
        assert "DATA_TEST" in details
        assert "🟡" in details
        assert "test.field" in details
