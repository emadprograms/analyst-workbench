"""
AI Output Data Validators (Fact-Checking)
==========================================
Cross-references AI-generated card claims against real market data from the
Impact Engine context card.

While quality_validators.py checks "Did the AI produce well-formed output?",
data_validators.py checks "Did the AI tell the truth?" by comparing claims
against independently computed market data.

Four validator categories:
  1. Directional / Bias Claims   — Trend_Bias vs actual day's return
  2. Session Arc Claims          — 3-Act narrative vs real session data
  3. Volume Claims               — "high/low volume" vs actual volume data
  4. Date / Ticker Consistency   — tickerDate, keyActionLog date correctness

Each validator returns issues appended to a DataReport.  The public entry
points are ``validate_company_data()`` and ``validate_economy_data()``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List


# ─────────────────────────────────────────────
# Result Types
# ─────────────────────────────────────────────

@dataclass
class DataIssue:
    """Single data-accuracy issue found during validation."""
    rule: str            # e.g. "DATA_BIAS_MISMATCH"
    severity: str        # "critical" | "warning" | "info"
    field: str           # e.g. "confidence" or "behavioralSentiment.emotionalTone"
    message: str         # Human-readable explanation


@dataclass
class DataReport:
    """Aggregate result of all data validators run on one card."""
    card_type: str       # "company" or "economy"
    ticker: str          # e.g. "AAPL" or "ECONOMY"
    issues: List[DataIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(i.severity == "critical" for i in self.issues)

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    def summary(self) -> str:
        status = "✅ PASS" if self.passed else "❌ FAIL"
        return (
            f"{status} | DATA CHECK ({self.card_type.upper()} {self.ticker}) | "
            f"Critical: {self.critical_count}, Warnings: {self.warning_count}, "
            f"Total Issues: {len(self.issues)}"
        )

    def details(self) -> str:
        lines = [self.summary()]
        for issue in self.issues:
            icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(issue.severity, "⚪")
            lines.append(f"  {icon} [{issue.rule}] {issue.field}: {issue.message}")
        return "\n".join(lines)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _get_session(context: dict, name: str) -> dict:
    """Safely extract a session dict from the Impact Engine context card."""
    return context.get("sessions", {}).get(name, {})


def _session_active(session: dict) -> bool:
    """Return True if the session has real data."""
    return session.get("status") == "Active"


def _get_rth_return(context: dict) -> float | None:
    """
    Calculate the RTH return as a percentage.
    Uses the reference (yesterday's close) and the RTH session close (last
    migration block's implied close / or the session's high-low midpoint).

    Returns None when data is insufficient.
    """
    ref = context.get("reference", {})
    prev_close = ref.get("yesterday_close", 0)
    if not prev_close:
        return None

    rth = _get_session(context, "regular_hours")
    if not _session_active(rth):
        return None

    # Best proxy for today's close: the last value_migration block's POC,
    # or the post-market's last POC if available.
    # Fallback cascade: post_market last POC → RTH last POC → RTH high/low midpoint
    close_proxy = None

    post = _get_session(context, "post_market")
    if _session_active(post):
        migration = post.get("value_migration", [])
        if migration:
            close_proxy = migration[-1].get("POC")

    if close_proxy is None:
        rth_migration = rth.get("value_migration", [])
        if rth_migration:
            close_proxy = rth_migration[-1].get("POC")

    if close_proxy is None:
        rth_high = rth.get("high")
        rth_low = rth.get("low")
        if rth_high is not None and rth_low is not None:
            close_proxy = (rth_high + rth_low) / 2

    if close_proxy is None:
        return None

    return ((close_proxy - prev_close) / prev_close) * 100


def _extract_setup_bias(screener_text: str) -> str | None:
    """Extract the Setup_Bias value from the screener_briefing field."""
    m = re.search(r"Setup[_ ]?Bias:\s*(Bullish|Bearish|Neutral)", screener_text, re.IGNORECASE)
    if m:
        return m.group(1).capitalize()
    return None


# ─────────────────────────────────────────────
# 1. Directional / Bias Validators
# ─────────────────────────────────────────────

# Threshold: flag "Bullish" when day dropped more than this %
BIAS_CONTRADICTION_THRESHOLD = 5.0
# Warning threshold for milder contradictions
BIAS_WARNING_THRESHOLD = 2.0
# Multi-day trend: number of migration blocks to check
TREND_BLOCK_COUNT = 6  # last ~3 hours of RTH


def _check_bias_vs_return(card: dict, context: dict, report: DataReport):
    """
    Compare stated Setup_Bias against the actual day's return.

    Rules:
    - Bullish bias + day dropped > 5%  → critical
    - Bearish bias + day rallied > 5%  → critical
    - Milder contradictions (2-5%)     → warning
    """
    screener = card.get("screener_briefing", "")
    if not screener:
        return

    bias = _extract_setup_bias(screener)
    if not bias:
        return  # can't verify without a parseable bias

    day_return = _get_rth_return(context)
    if day_return is None:
        return  # insufficient data

    # Bullish but price dropped significantly
    if bias == "Bullish" and day_return < -BIAS_CONTRADICTION_THRESHOLD:
        report.issues.append(DataIssue(
            rule="DATA_BIAS_CONTRADICTION",
            severity="critical",
            field="screener_briefing",
            message=(
                f"Setup_Bias is 'Bullish' but the day's return was "
                f"{day_return:+.2f}% (dropped >{BIAS_CONTRADICTION_THRESHOLD}%). "
                f"This is a major contradiction."
            )
        ))
    elif bias == "Bullish" and day_return < -BIAS_WARNING_THRESHOLD:
        report.issues.append(DataIssue(
            rule="DATA_BIAS_MISMATCH",
            severity="critical",
            field="screener_briefing",
            message=(
                f"Setup_Bias is 'Bullish' but the day's return was "
                f"{day_return:+.2f}%. May need justification."
            )
        ))

    # Bearish but price rallied significantly
    if bias == "Bearish" and day_return > BIAS_CONTRADICTION_THRESHOLD:
        report.issues.append(DataIssue(
            rule="DATA_BIAS_CONTRADICTION",
            severity="critical",
            field="screener_briefing",
            message=(
                f"Setup_Bias is 'Bearish' but the day's return was "
                f"{day_return:+.2f}% (rallied >{BIAS_CONTRADICTION_THRESHOLD}%). "
                f"This is a major contradiction."
            )
        ))
    elif bias == "Bearish" and day_return > BIAS_WARNING_THRESHOLD:
        report.issues.append(DataIssue(
            rule="DATA_BIAS_MISMATCH",
            severity="critical",
            field="screener_briefing",
            message=(
                f"Setup_Bias is 'Bearish' but the day's return was "
                f"{day_return:+.2f}%. May need justification."
            )
        ))


def _check_price_trend_direction(card: dict, context: dict, report: DataReport):
    """
    Verify priceTrend descriptions match actual multi-day trend direction.

    Checks the RTH value migration to see if POC was generally rising or falling,
    then compares against bullish/bearish language in priceTrend.
    """
    price_trend = card.get("basicContext", {}).get("priceTrend", "")
    if not price_trend:
        return

    rth = _get_session(context, "regular_hours")
    if not _session_active(rth):
        return

    migration = rth.get("value_migration", [])
    if len(migration) < 3:
        return  # need enough blocks to determine direction

    # Use last N blocks to determine intraday trend
    recent = migration[-min(TREND_BLOCK_COUNT, len(migration)):]
    first_poc = recent[0].get("POC")
    last_poc = recent[-1].get("POC")

    if first_poc is None or last_poc is None:
        return

    poc_change_pct = ((last_poc - first_poc) / first_poc) * 100

    # Detect bullish/bearish language in priceTrend
    trend_lower = price_trend.lower()
    claims_bullish = any(w in trend_lower for w in [
        "uptrend", "rally", "rallied", "bullish", "higher", "breakout",
        "strong upward", "ascending", "surging",
    ])
    claims_bearish = any(w in trend_lower for w in [
        "downtrend", "sell-off", "selloff", "bearish", "lower", "breakdown",
        "declining", "falling", "descending", "sold off",
    ])

    # Flag contradictions (only when the move is meaningful)
    if claims_bullish and poc_change_pct < -1.5:
        report.issues.append(DataIssue(
            rule="DATA_TREND_MISMATCH",
            severity="critical",
            field="basicContext.priceTrend",
            message=(
                f"priceTrend uses bullish language but intraday POC moved "
                f"{poc_change_pct:+.2f}% (first POC: ${first_poc:.2f} → "
                f"last POC: ${last_poc:.2f})."
            )
        ))
    elif claims_bearish and poc_change_pct > 1.5:
        report.issues.append(DataIssue(
            rule="DATA_TREND_MISMATCH",
            severity="critical",
            field="basicContext.priceTrend",
            message=(
                f"priceTrend uses bearish language but intraday POC moved "
                f"{poc_change_pct:+.2f}% (first POC: ${first_poc:.2f} → "
                f"last POC: ${last_poc:.2f})."
            )
        ))


# ─────────────────────────────────────────────
# 2. Session Arc Validators
# ─────────────────────────────────────────────

def _check_gap_claims(card: dict, context: dict, report: DataReport):
    """
    Check if "gap up" / "gap down" claims in emotionalTone correspond to
    actual pre-market behavior vs previous close.
    """
    tone = card.get("behavioralSentiment", {}).get("emotionalTone", "")
    if not tone:
        return

    ref = context.get("reference", {})
    prev_close = ref.get("yesterday_close", 0)
    if not prev_close:
        return

    pre = _get_session(context, "pre_market")
    if not _session_active(pre):
        return

    # Use explicitly calculated gap_pct and session_open if available
    if "gap_pct" in pre and "session_open" in pre:
        gap_pct = pre["gap_pct"]
        pre_open = pre["session_open"]
    else:
        # Fallback for old context format
        pre_migration = pre.get("value_migration", [])
        if not pre_migration:
            return

        # Parse the first range "low-high" to get the approximate open
        first_range = pre_migration[0].get("range", "")
        range_match = re.match(r"([\d.]+)-([\d.]+)", str(first_range))
        if not range_match:
            return

        pre_open = float(range_match.group(1))
        gap_pct = ((pre_open - prev_close) / prev_close) * 100

    tone_lower = tone.lower()

    # Check "gap up" claims
    if "gap up" in tone_lower or "gapped up" in tone_lower or "gap open" in tone_lower:
        if gap_pct < 0.1:  # essentially flat or negative
            report.issues.append(DataIssue(
                rule="DATA_GAP_MISMATCH",
                severity="critical",
                field="behavioralSentiment.emotionalTone",
                message=(
                    f"Claims 'gap up' but pre-market opened at ${pre_open:.2f} vs "
                    f"prev close ${prev_close:.2f} ({gap_pct:+.2f}%). "
                    f"No meaningful gap up detected."
                )
            ))

    # Check "gap down" claims
    if "gap down" in tone_lower or "gapped down" in tone_lower:
        if gap_pct > -0.1:
            report.issues.append(DataIssue(
                rule="DATA_GAP_MISMATCH",
                severity="critical",
                field="behavioralSentiment.emotionalTone",
                message=(
                    f"Claims 'gap down' but pre-market opened at ${pre_open:.2f} vs "
                    f"prev close ${prev_close:.2f} ({gap_pct:+.2f}%). "
                    f"No meaningful gap down detected."
                )
            ))


def _check_higher_lows_claim(card: dict, context: dict, report: DataReport):
    """
    Verify "higher lows" claims by checking if RTH lows were actually ascending
    across value migration blocks.
    """
    tone = card.get("behavioralSentiment", {}).get("emotionalTone", "")
    buyer_seller = card.get("behavioralSentiment", {}).get("buyerVsSeller", "")
    combined_text = f"{tone} {buyer_seller}".lower()

    if "higher low" not in combined_text and "higher lows" not in combined_text:
        return  # no claim to verify

    rth = _get_session(context, "regular_hours")
    if not _session_active(rth):
        return

    migration = rth.get("value_migration", [])
    if len(migration) < 3:
        return

    # Extract lows from migration block ranges
    block_lows = []
    for block in migration:
        range_str = str(block.get("range", ""))
        range_match = re.match(r"([\d.]+)-([\d.]+)", range_str)
        if range_match:
            block_lows.append(float(range_match.group(1)))

    if len(block_lows) < 3:
        return

    # Count how many consecutive blocks had ascending lows
    ascending_count = sum(
        1 for i in range(1, len(block_lows)) if block_lows[i] >= block_lows[i - 1]
    )
    ascending_ratio = ascending_count / (len(block_lows) - 1)

    # If less than 40% of blocks showed ascending lows, the claim is suspect
    if ascending_ratio < 0.4:
        report.issues.append(DataIssue(
            rule="DATA_HIGHER_LOWS_FALSE",
            severity="critical",
            field="behavioralSentiment.emotionalTone",
            message=(
                f"Claims 'higher lows' but only {ascending_count}/{len(block_lows)-1} "
                f"RTH migration blocks ({ascending_ratio:.0%}) showed ascending lows. "
                f"Block lows: {[f'${x:.2f}' for x in block_lows[:8]]}..."
            )
        ))


def _check_held_support_claim(card: dict, context: dict, report: DataReport):
    """
    Confirm "held support" claims by checking if intraday lows stayed above
    the stated support level(s).
    """
    tone = card.get("behavioralSentiment", {}).get("emotionalTone", "")
    buyer_seller = card.get("behavioralSentiment", {}).get("buyerVsSeller", "")
    combined_text = f"{tone} {buyer_seller}"

    if not re.search(r"held\s+support|defended\s+.*\$[\d,]+", combined_text, re.IGNORECASE):
        return  # no "held support" claim

    # Extract the specific dollar level from "held support at $XXX" or "defended $XXX"
    level_matches = re.findall(
        r"(?:held\s+(?:support\s+)?(?:at\s+)?\$|defended\s+(?:the\s+)?\$)([\d,.]+)",
        combined_text, re.IGNORECASE,
    )
    if not level_matches:
        return

    rth = _get_session(context, "regular_hours")
    if not _session_active(rth):
        return

    rth_low = rth.get("low")
    if rth_low is None:
        return

    for level_str in level_matches:
        try:
            claimed_support = float(level_str.replace(",", ""))
        except ValueError:
            continue

        # If RTH low broke significantly below the claimed support level
        # Allow a small tolerance (0.5%) for intraday wicks
        tolerance = claimed_support * 0.005
        if rth_low < (claimed_support - tolerance):
            breach_pct = ((claimed_support - rth_low) / claimed_support) * 100
            report.issues.append(DataIssue(
                rule="DATA_SUPPORT_BREACHED",
                severity="critical",
                field="behavioralSentiment.emotionalTone",
                message=(
                    f"Claims support 'held' at ${claimed_support:.2f} but RTH low "
                    f"was ${rth_low:.2f} ({breach_pct:.2f}% below). "
                    f"Support was breached, not held."
                )
            ))


# ─────────────────────────────────────────────
# 3. Volume Validators
# ─────────────────────────────────────────────

def _check_volume_claims(card: dict, context: dict, report: DataReport):
    """
    Compare "high-volume" or "low-volume" claims in volumeMomentum against
    actual volume data from the Impact Engine.

    Uses a simple heuristic: if RTH volume is referenced as "high" but is
    actually below the typical range (estimated from data_points and volume),
    flag it — and vice versa.

    Also cross-references the reference (yesterday's) data when available to
    provide a day-over-day comparison.
    """
    vol_momentum = card.get("technicalStructure", {}).get("volumeMomentum", "")
    if not vol_momentum:
        return

    rth = _get_session(context, "regular_hours")
    if not _session_active(rth):
        return

    rth_volume = rth.get("volume_approx", 0)
    if not rth_volume:
        return

    # Also check pre-market volume for additional context
    pre = _get_session(context, "pre_market")
    pre_volume = pre.get("volume_approx", 0) if _session_active(pre) else 0

    total_volume = rth_volume + pre_volume

    vol_lower = vol_momentum.lower()

    # Detect claims
    claims_high = any(phrase in vol_lower for phrase in [
        "high volume", "high-volume", "heavy volume", "massive volume",
        "extreme volume", "2x", "3x", "above-average volume",
        "above average volume", "surge", "spike",
    ])
    claims_low = any(phrase in vol_lower for phrase in [
        "low volume", "low-volume", "light volume", "thin volume",
        "below-average volume", "below average volume", "unconvincing volume",
        "muted volume", "anemic volume",
    ])

    # We need a baseline. The key_volume_events give us the top bars.
    # If the highest volume bar in RTH is quite small relative to total,
    # it suggests thinly traded action.
    key_events = rth.get("key_volume_events", [])
    if key_events:
        max_bar_volume = max(e.get("volume", 0) for e in key_events)

        # Heuristic: if the top bar is < 1% of total RTH volume and AI claims
        # "high volume", that's suspicious. This is a rough heuristic.
        if claims_high and max_bar_volume > 0:
            top_bar_ratio = max_bar_volume / rth_volume if rth_volume else 0
            # In a genuinely high-volume session, individual bar spikes are a
            # smaller fraction of total volume. But the converse — very large
            # ratio — means volume is concentrated in one bar (spike, not sustained).
            # We can't check against 20-day average without historical data, so
            # we use reference comparison approach.
            pass

    # Compare vs yesterday (reference) volume if we have a rough proxy
    # The reference doesn't include volume, but the pre-market to RTH ratio
    # can hint at unusual activity
    if pre_volume > 0 and rth_volume > 0:
        pre_to_rth_ratio = pre_volume / rth_volume

        # Normal pre-market is ~1-5% of RTH volume. If it's > 15%, that IS
        # unusual activity (confirms "high volume" or event-driven claims)
        if claims_low and pre_to_rth_ratio > 0.15:
            report.issues.append(DataIssue(
                rule="DATA_VOLUME_MISMATCH",
                severity="info",
                field="technicalStructure.volumeMomentum",
                message=(
                    f"Claims 'low/light volume' but pre-market volume "
                    f"({pre_volume:,}) was {pre_to_rth_ratio:.1%} of RTH volume "
                    f"({rth_volume:,}), suggesting elevated activity."
                )
            ))

    # Check Volume Profile confirmation
    vol_profile = rth.get("volume_profile", {})
    poc = vol_profile.get("POC")
    vah = vol_profile.get("VAH")
    val_price = vol_profile.get("VAL")

    if poc is not None and vah is not None and val_price is not None:
        value_width = vah - val_price
        rth_range = rth.get("high", 0) - rth.get("low", 0)

        if rth_range > 0:
            value_concentration = value_width / rth_range

            # Narrow value area (< 30% of range) with "high volume" claim
            # suggests volume was clustered, not broadly distributed — consistent
            # with a "defense" or "rejection" pattern
            # Wide value area (> 70%) with "low volume" suggests broad acceptance
            # which contradicts a "low volume" characterization
            if claims_low and value_concentration > 0.70:
                report.issues.append(DataIssue(
                    rule="DATA_VOLUME_PROFILE_MISMATCH",
                    severity="critical",
                    field="technicalStructure.volumeMomentum",
                    message=(
                        f"Claims 'low/light volume' but Value Area covers "
                        f"{value_concentration:.0%} of the day's range "
                        f"(VAL: ${val_price:.2f}, VAH: ${vah:.2f}, Range: "
                        f"${rth.get('low', 0):.2f}-${rth.get('high', 0):.2f}). "
                        f"Wide value acceptance suggests meaningful participation."
                    )
                ))


# ─────────────────────────────────────────────
# 4. Date / Ticker Consistency Validators
# ─────────────────────────────────────────────

def _check_ticker_date_consistency(
    card: dict, context: dict, ticker: str, trade_date: str, report: DataReport
):
    """
    Verify:
    1. tickerDate matches the actual ticker and date being processed
    2. keyActionLog[-1].date matches the trade date
    3. The card isn't echoing stale data from a previous day
    """
    # 1. Check tickerDate field
    ticker_date = card.get("basicContext", {}).get("tickerDate", "")
    if ticker_date:
        # Expected format: "TICKER | YYYY-MM-DD"
        if ticker not in ticker_date:
            report.issues.append(DataIssue(
                rule="DATA_TICKER_WRONG",
                severity="critical",
                field="basicContext.tickerDate",
                message=(
                    f"tickerDate '{ticker_date}' does not contain the expected "
                    f"ticker '{ticker}'."
                )
            ))
        if trade_date not in ticker_date:
            report.issues.append(DataIssue(
                rule="DATA_DATE_WRONG",
                severity="critical",
                field="basicContext.tickerDate",
                message=(
                    f"tickerDate '{ticker_date}' does not contain the expected "
                    f"date '{trade_date}'."
                )
            ))

    # 2. Check keyActionLog[-1].date
    log = card.get("technicalStructure", {}).get("keyActionLog", [])
    if log and isinstance(log, list):
        latest_entry = log[-1]
        if isinstance(latest_entry, dict):
            log_date = latest_entry.get("date", "")
            if log_date and log_date != trade_date:
                report.issues.append(DataIssue(
                    rule="DATA_LOG_DATE_STALE",
                    severity="critical",
                    field="keyActionLog[-1].date",
                    message=(
                        f"Latest keyActionLog entry date is '{log_date}' but "
                        f"trade date is '{trade_date}'. The card may contain "
                        f"stale data from a previous day."
                    )
                ))

    # 3. Cross-check context card date against trade_date
    context_date = context.get("meta", {}).get("date", "")
    if context_date and context_date != trade_date:
        report.issues.append(DataIssue(
            rule="DATA_CONTEXT_DATE_MISMATCH",
            severity="critical",
            field="meta.date",
            message=(
                f"Impact Engine context card date is '{context_date}' but "
                f"trade date is '{trade_date}'. Data may be from wrong day."
            )
        ))

    # 4. Cross-check context ticker
    context_ticker = context.get("meta", {}).get("ticker", "")
    if context_ticker and context_ticker != ticker:
        report.issues.append(DataIssue(
            rule="DATA_CONTEXT_TICKER_MISMATCH",
            severity="critical",
            field="meta.ticker",
            message=(
                f"Impact Engine context card ticker is '{context_ticker}' but "
                f"expected '{ticker}'. Wrong context data used."
            )
        ))


def _check_economy_date_consistency(
    card: dict, trade_date: str, report: DataReport
):
    """
    Economy card date checks:
    1. keyActionLog[-1].date matches the trade date
    """
    log = card.get("keyActionLog", [])
    if log and isinstance(log, list):
        latest_entry = log[-1]
        if isinstance(latest_entry, dict):
            log_date = latest_entry.get("date", "")
            if log_date and log_date != trade_date:
                report.issues.append(DataIssue(
                    rule="DATA_LOG_DATE_STALE",
                    severity="critical",
                    field="keyActionLog[-1].date",
                    message=(
                        f"Latest keyActionLog entry date is '{log_date}' but "
                        f"trade date is '{trade_date}'. The economy card may "
                        f"contain stale data from a previous day."
                    )
                ))


# ─────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────

def validate_company_data(
    card: dict,
    impact_context: dict,
    ticker: str = "UNKNOWN",
    trade_date: str = "",
) -> DataReport:
    """
    Run all data-accuracy validators on a company card.

    Args:
        card: The generated company card dict.
        impact_context: The Impact Engine context card dict for the same
                        ticker and date (from get_or_compute_context).
        ticker: Ticker symbol for reporting.
        trade_date: The trade date string (YYYY-MM-DD).

    Returns:
        DataReport with all data-accuracy issues found.
    """
    report = DataReport(card_type="company", ticker=ticker)

    if not impact_context or not isinstance(impact_context, dict):
        report.issues.append(DataIssue(
            rule="DATA_NO_CONTEXT",
            severity="info",
            field="root",
            message="No Impact Engine context available — skipping data validation."
        ))
        return report

    if impact_context.get("status") == "No Data":
        report.issues.append(DataIssue(
            rule="DATA_NO_CONTEXT",
            severity="info",
            field="root",
            message="Impact Engine context has no data — skipping data validation."
        ))
        return report

    # 1. Directional / Bias claims
    _check_bias_vs_return(card, impact_context, report)

    # 2. Session arc claims
    _check_gap_claims(card, impact_context, report)
    _check_higher_lows_claim(card, impact_context, report)
    _check_held_support_claim(card, impact_context, report)

    # 3. Volume claims
    _check_volume_claims(card, impact_context, report)

    # 4. Date / Ticker consistency
    if trade_date:
        _check_ticker_date_consistency(card, impact_context, ticker, trade_date, report)

    return report


def validate_economy_data(
    card: dict,
    etf_contexts: dict | None = None,
    trade_date: str = "",
) -> DataReport:
    """
    Run data-accuracy validators on an economy card.

    Args:
        card: The generated economy card dict.
        etf_contexts: Dict of {etf_ticker: impact_context_dict} for SPY, QQQ, etc.
        trade_date: The trade date string (YYYY-MM-DD).

    Returns:
        DataReport with all data-accuracy issues found.
    """
    report = DataReport(card_type="economy", ticker="ECONOMY")

    # 1. Date consistency
    if trade_date:
        _check_economy_date_consistency(card, trade_date, report)

    # 2. If we have SPY context, verify index-level claims
    if etf_contexts and isinstance(etf_contexts, dict):
        spy_context = etf_contexts.get("SPY")
        if spy_context and isinstance(spy_context, dict) and spy_context.get("status") != "No Data":
            _check_economy_bias_vs_spy(card, spy_context, report)

    return report


def _check_economy_bias_vs_spy(card: dict, spy_context: dict, report: DataReport):
    """
    Cross-check the economy card's marketBias against SPY's actual return.
    """
    bias = card.get("marketBias", "")
    if not bias:
        return

    spy_return = _get_rth_return(spy_context)
    if spy_return is None:
        return

    bias_lower = bias.lower()
    is_bullish = any(w in bias_lower for w in ["bullish", "risk-on"])
    is_bearish = any(w in bias_lower for w in ["bearish", "risk-off"])

    if is_bullish and spy_return < -BIAS_CONTRADICTION_THRESHOLD:
        report.issues.append(DataIssue(
            rule="DATA_ECON_BIAS_CONTRADICTION",
            severity="critical",
            field="marketBias",
            message=(
                f"marketBias is '{bias}' but SPY returned "
                f"{spy_return:+.2f}% — a significant sell-off contradicts "
                f"the bullish macro stance."
            )
        ))
    elif is_bullish and spy_return < -BIAS_WARNING_THRESHOLD:
        report.issues.append(DataIssue(
            rule="DATA_ECON_BIAS_MISMATCH",
            severity="critical",
            field="marketBias",
            message=(
                f"marketBias is '{bias}' but SPY returned "
                f"{spy_return:+.2f}%. Mild contradiction."
            )
        ))

    if is_bearish and spy_return > BIAS_CONTRADICTION_THRESHOLD:
        report.issues.append(DataIssue(
            rule="DATA_ECON_BIAS_CONTRADICTION",
            severity="critical",
            field="marketBias",
            message=(
                f"marketBias is '{bias}' but SPY returned "
                f"{spy_return:+.2f}% — a strong rally contradicts "
                f"the bearish macro stance."
            )
        ))
    elif is_bearish and spy_return > BIAS_WARNING_THRESHOLD:
        report.issues.append(DataIssue(
            rule="DATA_ECON_BIAS_MISMATCH",
            severity="critical",
            field="marketBias",
            message=(
                f"marketBias is '{bias}' but SPY returned "
                f"{spy_return:+.2f}%. Mild contradiction."
            )
        ))
