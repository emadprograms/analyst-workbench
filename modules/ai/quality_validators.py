"""
AI Output Quality Validators
=============================
Reusable validators that check whether AI-generated cards meet quality standards.
These can be used in:
  1. Unit tests (tests/test_ai_quality.py) — with sample fixtures
  2. Production pipeline — call validate_company_card() after each AI generation
  3. CI/CD — as regression gates

Each validator returns a QualityResult with pass/fail, severity, and a human-readable reason.
"""
from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from typing import List


# ─────────────────────────────────────────────
# Result Types
# ─────────────────────────────────────────────

@dataclass
class QualityIssue:
    """Single quality issue found during validation."""
    rule: str            # e.g. "SCHEMA_001"
    severity: str        # "critical" | "warning" | "info"
    field: str           # e.g. "todaysAction" or "confidence"
    message: str         # Human-readable explanation

@dataclass
class QualityReport:
    """Aggregate result of all validators run on one card."""
    card_type: str       # "company" or "economy"
    ticker: str          # e.g. "AAPL" or "ECONOMY"
    issues: List[QualityIssue] = field(default_factory=list)

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
            f"{status} | {self.card_type.upper()} ({self.ticker}) | "
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
# Company Card Validators
# ─────────────────────────────────────────────

# Required top-level and nested fields for company cards
COMPANY_REQUIRED_SCHEMA = {
    "marketNote": str,
    "confidence": str,
    "screener_briefing": str,
    "basicContext": {
        "tickerDate": str,
        "sector": str,
        "companyDescription": str,
        "priceTrend": str,
        "recentCatalyst": str,
    },
    "technicalStructure": {
        "majorSupport": str,
        "majorResistance": str,
        "pattern": str,
        "keyActionLog": list,
        "volumeMomentum": str,
    },
    "fundamentalContext": {
        "valuation": str,
        "analystSentiment": str,
        "insiderActivity": str,
        "peerPerformance": str,
    },
    "behavioralSentiment": {
        "buyerVsSeller": str,
        "emotionalTone": str,
        "newsReaction": str,
    },
    "openingTradePlan": {
        "planName": str,
        "knownParticipant": str,
        "expectedParticipant": str,
        "trigger": str,
        "invalidation": str,
    },
    "alternativePlan": {
        "planName": str,
        "scenario": str,
        "knownParticipant": str,
        "expectedParticipant": str,
        "trigger": str,
        "invalidation": str,
    },
}

# Required fields for economy cards
ECONOMY_REQUIRED_SCHEMA = {
    "marketNarrative": str,
    "marketBias": str,
    "keyActionLog": list,
    "keyEconomicEvents": {
        "last_24h": str,
        "next_24h": str,
    },
    "sectorRotation": {
        "leadingSectors": list,
        "laggingSectors": list,
        "rotationAnalysis": str,
    },
    "indexAnalysis": {
        "pattern": str,
        "SPY": str,
        "QQQ": str,
    },
    "interMarketAnalysis": {
        "bonds": str,
        "commodities": str,
        "currencies": str,
        "crypto": str,
    },
    "marketInternals": {
        "volatility": str,
    },
}


def _check_schema(card: dict, schema: dict, report: QualityReport, prefix: str = ""):
    """Recursively check that all required fields exist and have correct types."""
    for key, expected in schema.items():
        full_path = f"{prefix}.{key}" if prefix else key

        if key not in card:
            report.issues.append(QualityIssue(
                rule="SCHEMA_MISSING",
                severity="critical",
                field=full_path,
                message=f"Required field '{full_path}' is missing from the card."
            ))
            continue

        value = card[key]

        if isinstance(expected, dict):
            # Nested object — recurse
            if not isinstance(value, dict):
                report.issues.append(QualityIssue(
                    rule="SCHEMA_TYPE",
                    severity="critical",
                    field=full_path,
                    message=f"Expected dict, got {type(value).__name__}."
                ))
            else:
                _check_schema(value, expected, report, prefix=full_path)
        elif expected is list:
            if not isinstance(value, list):
                report.issues.append(QualityIssue(
                    rule="SCHEMA_TYPE",
                    severity="critical",
                    field=full_path,
                    message=f"Expected list, got {type(value).__name__}."
                ))
        elif expected is str:
            if not isinstance(value, str):
                report.issues.append(QualityIssue(
                    rule="SCHEMA_TYPE",
                    severity="critical",
                    field=full_path,
                    message=f"Expected str, got {type(value).__name__}."
                ))


# ─────────────────────────────────────────────
# Placeholder / Default Text Detection
# ─────────────────────────────────────────────

# Phrases that indicate the AI echoed the prompt instructions instead of
# producing real analytical content.
PLACEHOLDER_PATTERNS = [
    r"AI Updates:",
    r"AI RULE:",
    r"Set in Static Editor",
    r"Set during initialization",
    r"Your \*?new\*? ",          # "Your new summary" / "Your *new* summary"
    r"Your \*?evolved\*? ",      # Prompt instruction text leaked
    r"Your \*?first\*? output",
    r"Carry over from \[Previous Card\]",
    r"AI will provide",
]

# Fields where these placeholders are checked
# (valuation is READ-ONLY so its placeholder is expected)
PLACEHOLDER_EXEMPT_FIELDS = {"fundamentalContext.valuation"}


def _check_placeholder_text(card: dict, report: QualityReport, prefix: str = ""):
    """Detect fields where AI echoed prompt instructions instead of real analysis."""
    for key, value in card.items():
        full_path = f"{prefix}.{key}" if prefix else key

        if isinstance(value, dict):
            _check_placeholder_text(value, report, prefix=full_path)
        elif isinstance(value, str) and full_path not in PLACEHOLDER_EXEMPT_FIELDS:
            for pattern in PLACEHOLDER_PATTERNS:
                if re.search(pattern, value, re.IGNORECASE):
                    report.issues.append(QualityIssue(
                        rule="CONTENT_PLACEHOLDER",
                        severity="critical",
                        field=full_path,
                        message=f"Field contains prompt placeholder text: '{value[:120]}...'"
                    ))
                    break  # one match per field is enough


# ─────────────────────────────────────────────
# todaysAction Quality Checks
# ─────────────────────────────────────────────

def _check_todays_action_quality(card: dict, report: QualityReport, card_type: str):
    """
    Validates the todaysAction / keyActionLog entries.
    Rules:
      - todaysAction must exist (in the latest keyActionLog entry)
      - Must be under 2500 characters
      - Must contain a date stamp
      - Must NOT contain screener_briefing content (S_Levels, R_Levels, Setup_Bias)
      - Must NOT contain structured field names (majorSupport, volumeMomentum, etc.)
    """
    if card_type == "company":
        log = card.get("technicalStructure", {}).get("keyActionLog", [])
    else:
        log = card.get("keyActionLog", [])

    if not log or not isinstance(log, list):
        report.issues.append(QualityIssue(
            rule="ACTION_LOG_MISSING",
            severity="warning",
            field="keyActionLog",
            message="keyActionLog is missing or empty."
        ))
        return

    latest_entry = log[-1]
    if not isinstance(latest_entry, dict):
        report.issues.append(QualityIssue(
            rule="ACTION_LOG_FORMAT",
            severity="critical",
            field="keyActionLog[-1]",
            message=f"Latest log entry is not a dict: {type(latest_entry).__name__}"
        ))
        return

    action_text = latest_entry.get("action", "")
    action_date = latest_entry.get("date", "")

    if not action_text:
        report.issues.append(QualityIssue(
            rule="ACTION_EMPTY",
            severity="critical",
            field="keyActionLog[-1].action",
            message="todaysAction is empty."
        ))
        return

    if not action_date:
        report.issues.append(QualityIssue(
            rule="ACTION_NO_DATE",
            severity="warning",
            field="keyActionLog[-1].date",
            message="Log entry is missing a date stamp."
        ))

    # LENGTH CHECK
    if len(action_text) > 2500:
        report.issues.append(QualityIssue(
            rule="ACTION_TOO_LONG",
            severity="critical",
            field="keyActionLog[-1].action",
            message=f"todaysAction is {len(action_text)} chars (limit: 2500). "
                    f"Preview: '{action_text[:100]}...'"
        ))

    # CARD-DUMP DETECTION: Check for structured content that belongs in other fields
    dump_patterns = [
        (r"S_Levels:", "screener_briefing content (S_Levels)"),
        (r"R_Levels:", "screener_briefing content (R_Levels)"),
        (r"Setup_Bias:", "screener_briefing content (Setup_Bias)"),
        (r"Plan_A:", "screener_briefing content (Plan_A)"),
        (r"Plan_B:", "screener_briefing content (Plan_B)"),
        (r"majorSupport", "technicalStructure field name"),
        (r"majorResistance", "technicalStructure field name"),
        (r"volumeMomentum", "technicalStructure field name"),
        (r"behavioralSentiment", "behavioralSentiment field name"),
        (r"fundamentalContext", "fundamentalContext field name"),
        (r"screener_briefing", "screener_briefing field name"),
        (r"openingTradePlan", "openingTradePlan field name"),
        (r"alternativePlan", "alternativePlan field name"),
    ]
    for pattern, desc in dump_patterns:
        if re.search(pattern, action_text, re.IGNORECASE):
            report.issues.append(QualityIssue(
                rule="ACTION_CARD_DUMP",
                severity="critical",
                field="keyActionLog[-1].action",
                message=f"todaysAction contains {desc}. It should be a concise narrative, "
                        f"not a card-level dump."
            ))


# ─────────────────────────────────────────────
# Confidence Field Quality
# ─────────────────────────────────────────────

def _check_confidence_format(card: dict, report: QualityReport):
    """
    Company card 'confidence' field must follow the format:
    "Trend_Bias: <Bias> (Story_Confidence: <High|Medium|Low>) - Reasoning: <text>"
    """
    confidence = card.get("confidence", "")
    if not confidence:
        return  # already caught by schema check

    # Must contain Trend_Bias
    if "Trend_Bias" not in confidence and "Trend_Bias" not in confidence.replace("_", " "):
        report.issues.append(QualityIssue(
            rule="CONFIDENCE_NO_BIAS",
            severity="critical",
            field="confidence",
            message=f"'confidence' is missing 'Trend_Bias' label. Got: '{confidence[:100]}'"
        ))

    # Must contain Story_Confidence
    if "Story_Confidence" not in confidence and "Story_Confidence" not in confidence.replace("_", " "):
        report.issues.append(QualityIssue(
            rule="CONFIDENCE_NO_STORY",
            severity="warning",
            field="confidence",
            message=f"'confidence' is missing 'Story_Confidence' rating. Got: '{confidence[:100]}'"
        ))
    else:
        # If present, must be one of High/Medium/Low
        sc_match = re.search(r"Story[_ ]?Confidence:\s*(High|Medium|Low)", confidence, re.IGNORECASE)
        if not sc_match:
            report.issues.append(QualityIssue(
                rule="CONFIDENCE_BAD_RATING",
                severity="warning",
                field="confidence",
                message=f"Story_Confidence value must be High, Medium, or Low. Got: '{confidence[:100]}'"
            ))

    # Should contain some reasoning
    if "Reasoning" not in confidence and "reasoning" not in confidence.lower():
        report.issues.append(QualityIssue(
            rule="CONFIDENCE_NO_REASONING",
            severity="warning",
            field="confidence",
            message="'confidence' field is missing a 'Reasoning' section."
        ))


# ─────────────────────────────────────────────
# Screener Briefing Quality
# ─────────────────────────────────────────────

SCREENER_REQUIRED_KEYS = [
    "Setup_Bias",
    "Justification",
    "Catalyst",
    "Pattern",
    "Plan_A",
    "Plan_B",
    "S_Levels",
    "R_Levels",
]

def _check_screener_briefing(card: dict, report: QualityReport):
    """
    screener_briefing must be a multi-line key:value text block containing
    all required keys from the prompt spec.
    """
    briefing = card.get("screener_briefing", "")
    if not briefing:
        return  # caught by schema check

    for key in SCREENER_REQUIRED_KEYS:
        # Match "Key:" at the start of a line or after a newline
        pattern = rf"(?:^|\n)\s*{re.escape(key)}\s*:"
        if not re.search(pattern, briefing):
            report.issues.append(QualityIssue(
                rule="SCREENER_MISSING_KEY",
                severity="warning",
                field="screener_briefing",
                message=f"screener_briefing is missing required key '{key}'."
            ))

    # Setup_Bias must be one of the expected values
    bias_match = re.search(
        r"Setup_Bias:\s*(.+?)(?:\n|$)", briefing
    )
    if bias_match:
        bias_val = bias_match.group(1).strip()
        valid_biases = [
            "Bullish", "Bearish", "Neutral",
            "Neutral (Bullish Lean)", "Neutral (Bearish Lean)",
        ]
        # Fuzzy: check if the value starts with one of the valid options
        if not any(bias_val.startswith(vb) for vb in valid_biases):
            report.issues.append(QualityIssue(
                rule="SCREENER_BAD_BIAS",
                severity="warning",
                field="screener_briefing.Setup_Bias",
                message=f"Setup_Bias value '{bias_val}' is not a recognized bias label."
            ))


# ─────────────────────────────────────────────
# Behavioral Sentiment / emotionalTone Quality
# ─────────────────────────────────────────────

# The prompt defines these 5 patterns from the masterclass
VALID_PATTERNS = [
    "Accumulation",
    "Capitulation",
    "Stable Uptrend",
    "Washout",  # includes "Washout & Reclaim"
    "Chop",
    "Breakout",
    "Breakdown",
    "Distribution",
    "Squeeze",
    "Compression",
    "Balance",
    "Consolidation",
    "Reversal",
    "Continuation",
    "Exhaustion",
]

# Market state labels from the masterclass
VALID_STATES = ["Stable", "Unstable", "Hybrid"]

# 4-Participant model terms that should appear in analytical fields
PARTICIPANT_TERMS = [
    "Committed Buyer",
    "Committed Seller",
    "Desperate Buyer",
    "Desperate Seller",
    "committed buyer",
    "committed seller",
    "desperate buyer",
    "desperate seller",
]


def _check_emotional_tone(card: dict, report: QualityReport):
    """
    emotionalTone must:
    1. Contain a recognized pattern label
    2. Contain a market state label (Stable/Unstable)
    3. Include a 'Reasoning' with the 3-Act structure (Act I, Act II, Act III)
    """
    tone = card.get("behavioralSentiment", {}).get("emotionalTone", "")
    if not tone:
        return

    # Check for pattern label
    has_pattern = any(p.lower() in tone.lower() for p in VALID_PATTERNS)
    if not has_pattern:
        report.issues.append(QualityIssue(
            rule="TONE_NO_PATTERN",
            severity="warning",
            field="behavioralSentiment.emotionalTone",
            message=f"emotionalTone doesn't contain a recognized pattern label "
                    f"(e.g., Accumulation, Capitulation, Chop). Got: '{tone[:80]}...'"
        ))

    # Check for state label
    has_state = any(s.lower() in tone.lower() for s in VALID_STATES)
    if not has_state:
        report.issues.append(QualityIssue(
            rule="TONE_NO_STATE",
            severity="info",
            field="behavioralSentiment.emotionalTone",
            message="emotionalTone doesn't contain a market state label (Stable/Unstable)."
        ))

    # Check for 3-Act reasoning structure
    act_count = sum(1 for act in ["Act I", "Act II", "Act III", "Act 1", "Act 2", "Act 3"]
                    if act.lower() in tone.lower())
    if act_count < 2:
        report.issues.append(QualityIssue(
            rule="TONE_NO_ACTS",
            severity="warning",
            field="behavioralSentiment.emotionalTone",
            message="emotionalTone is missing the 3-Act reasoning structure "
                    "(Act I: Intent, Act II: Conflict, Act III: Resolution)."
        ))


def _check_participant_language(card: dict, report: QualityReport):
    """
    The AI should use 4-Participant Model terminology in its analysis.
    Check key analytical fields for participant references.
    """
    fields_to_check = [
        ("behavioralSentiment.buyerVsSeller", card.get("behavioralSentiment", {}).get("buyerVsSeller", "")),
        ("behavioralSentiment.emotionalTone", card.get("behavioralSentiment", {}).get("emotionalTone", "")),
        ("openingTradePlan.knownParticipant", card.get("openingTradePlan", {}).get("knownParticipant", "")),
        ("openingTradePlan.expectedParticipant", card.get("openingTradePlan", {}).get("expectedParticipant", "")),
        ("alternativePlan.knownParticipant", card.get("alternativePlan", {}).get("knownParticipant", "")),
        ("alternativePlan.expectedParticipant", card.get("alternativePlan", {}).get("expectedParticipant", "")),
    ]

    # At minimum, the participant fields should reference the model
    for field_path, text in fields_to_check:
        if not text:
            continue
        if "Participant" in field_path:
            has_participant = any(term.lower() in text.lower() for term in PARTICIPANT_TERMS)
            if not has_participant:
                report.issues.append(QualityIssue(
                    rule="PARTICIPANT_MISSING",
                    severity="warning",
                    field=field_path,
                    message=f"Expected 4-Participant Model terminology "
                            f"(e.g., 'Committed Buyers'). Got: '{text[:80]}'"
                ))


# ─────────────────────────────────────────────
# Trade Plan Quality
# ─────────────────────────────────────────────

def _check_trade_plans(card: dict, report: QualityReport):
    """
    Both openingTradePlan and alternativePlan should:
    - Have a concrete planName (not generic placeholder)
    - Have price-level references in trigger/invalidation
    """
    for plan_key in ["openingTradePlan", "alternativePlan"]:
        plan = card.get(plan_key, {})
        if not plan or not isinstance(plan, dict):
            continue

        plan_name = plan.get("planName", "")
        trigger = plan.get("trigger", "")
        invalidation = plan.get("invalidation", "")

        # Check for price reference in trigger (should contain $ or a number)
        if trigger and not re.search(r"\$[\d,]+\.?\d*|\d{2,}", trigger):
            report.issues.append(QualityIssue(
                rule="PLAN_NO_PRICE",
                severity="warning",
                field=f"{plan_key}.trigger",
                message=f"Trigger should reference a specific price level. Got: '{trigger[:80]}'"
            ))

        # Check for price reference in invalidation
        if invalidation and not re.search(r"\$[\d,]+\.?\d*|\d{2,}", invalidation):
            report.issues.append(QualityIssue(
                rule="PLAN_NO_PRICE",
                severity="warning",
                field=f"{plan_key}.invalidation",
                message=f"Invalidation should reference a specific price level. Got: '{invalidation[:80]}'"
            ))


# ─────────────────────────────────────────────
# Economy Card Specific Validators
# ─────────────────────────────────────────────

VALID_MARKET_BIASES = [
    "Bullish", "Bearish", "Neutral",
    "Cautiously Bullish", "Cautiously Bearish",
    "Risk-On", "Risk-Off",
]


def _check_economy_bias(card: dict, report: QualityReport):
    """marketBias should be a recognized label."""
    bias = card.get("marketBias", "")
    if not bias:
        return

    if not any(vb.lower() in bias.lower() for vb in VALID_MARKET_BIASES):
        report.issues.append(QualityIssue(
            rule="ECON_BAD_BIAS",
            severity="warning",
            field="marketBias",
            message=f"marketBias '{bias}' is not a recognized bias label."
        ))


def _check_economy_sectors(card: dict, report: QualityReport):
    """sectorRotation should have at least one leading and one lagging sector."""
    rotation = card.get("sectorRotation", {})
    leading = rotation.get("leadingSectors", [])
    lagging = rotation.get("laggingSectors", [])

    if isinstance(leading, list) and len(leading) == 0:
        report.issues.append(QualityIssue(
            rule="ECON_NO_LEADING",
            severity="warning",
            field="sectorRotation.leadingSectors",
            message="leadingSectors is empty — AI should identify sector leaders."
        ))
    if isinstance(lagging, list) and len(lagging) == 0:
        report.issues.append(QualityIssue(
            rule="ECON_NO_LAGGING",
            severity="warning",
            field="sectorRotation.laggingSectors",
            message="laggingSectors is empty — AI should identify sector laggards."
        ))


# ─────────────────────────────────────────────
# Content Substance Checks (Empty / Too Short)
# ─────────────────────────────────────────────

# Fields that must have substantive content (> N chars)
COMPANY_SUBSTANTIVE_FIELDS = {
    "confidence": 40,
    "screener_briefing": 80,
    "basicContext.priceTrend": 10,
    "basicContext.recentCatalyst": 10,
    "technicalStructure.majorSupport": 5,
    "technicalStructure.majorResistance": 5,
    "technicalStructure.pattern": 15,
    "technicalStructure.volumeMomentum": 15,
    "behavioralSentiment.buyerVsSeller": 15,
    "behavioralSentiment.emotionalTone": 20,
    "behavioralSentiment.newsReaction": 10,
}

ECONOMY_SUBSTANTIVE_FIELDS = {
    "marketNarrative": 30,
    "marketBias": 3,
    "indexAnalysis.SPY": 10,
    "indexAnalysis.QQQ": 10,
    "indexAnalysis.pattern": 10,
    "interMarketAnalysis.bonds": 10,
    "marketInternals.volatility": 5,
}


def _check_substance(card: dict, min_lengths: dict, report: QualityReport):
    """Check that key analytical fields aren't empty or trivially short."""
    for dotted_path, min_len in min_lengths.items():
        parts = dotted_path.split(".")
        value = card
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part, "")
            else:
                value = ""
                break

        if isinstance(value, str) and len(value.strip()) < min_len:
            report.issues.append(QualityIssue(
                rule="CONTENT_THIN",
                severity="warning",
                field=dotted_path,
                message=f"Field is too short ({len(value.strip())} chars, min {min_len}). "
                        f"Content: '{value.strip()[:60]}'"
            ))


# ─────────────────────────────────────────────
# Valuation Preservation Check
# ─────────────────────────────────────────────

def _check_valuation_preserved(card: dict, previous_card: dict | None, report: QualityReport):
    """
    The 'valuation' field is READ-ONLY per prompt rules.
    If a previous card is provided, check that valuation was preserved.
    """
    if previous_card is None:
        return

    prev_val = previous_card.get("fundamentalContext", {}).get("valuation", "")
    new_val = card.get("fundamentalContext", {}).get("valuation", "")

    if prev_val and new_val and prev_val != new_val:
        # Allow if the new value looks like real data (not placeholder)
        if "READ-ONLY" in new_val or "AI RULE" in new_val:
            report.issues.append(QualityIssue(
                rule="VALUATION_OVERWRITTEN",
                severity="critical",
                field="fundamentalContext.valuation",
                message=f"READ-ONLY 'valuation' was overwritten with placeholder text. "
                        f"Previous: '{prev_val[:60]}', New: '{new_val[:60]}'"
            ))


# ─────────────────────────────────────────────
# PUBLIC API: Main Validator Functions
# ─────────────────────────────────────────────

def validate_company_card(
    card: dict | str,
    ticker: str = "UNKNOWN",
    previous_card: dict | None = None,
) -> QualityReport:
    """
    Run all company card quality validators.

    Args:
        card: The generated company card (dict or JSON string).
        ticker: Ticker symbol for reporting.
        previous_card: The previous card (for read-only preservation checks).

    Returns:
        QualityReport with all issues found.
    """
    if isinstance(card, str):
        try:
            card = json.loads(card)
        except json.JSONDecodeError:
            report = QualityReport(card_type="company", ticker=ticker)
            report.issues.append(QualityIssue(
                rule="PARSE_FAIL",
                severity="critical",
                field="root",
                message="Card is not valid JSON."
            ))
            return report

    if not isinstance(card, dict):
        report = QualityReport(card_type="company", ticker=ticker)
        report.issues.append(QualityIssue(
            rule="PARSE_FAIL",
            severity="critical",
            field="root",
            message=f"Card parsed to {type(card).__name__}, expected dict."
        ))
        return report

    report = QualityReport(card_type="company", ticker=ticker)

    # 1. Schema completeness
    _check_schema(card, COMPANY_REQUIRED_SCHEMA, report)

    # 2. Placeholder detection
    _check_placeholder_text(card, report)

    # 3. todaysAction quality
    _check_todays_action_quality(card, report, "company")

    # 4. Confidence format
    _check_confidence_format(card, report)

    # 5. Screener briefing
    _check_screener_briefing(card, report)

    # 6. Emotional tone / 3-Act structure
    _check_emotional_tone(card, report)

    # 7. 4-Participant language
    _check_participant_language(card, report)

    # 8. Trade plans
    _check_trade_plans(card, report)

    # 9. Content substance
    _check_substance(card, COMPANY_SUBSTANTIVE_FIELDS, report)

    # 10. Valuation preservation
    _check_valuation_preserved(card, previous_card, report)

    return report


def validate_economy_card(
    card: dict | str,
) -> QualityReport:
    """
    Run all economy card quality validators.

    Args:
        card: The generated economy card (dict or JSON string).

    Returns:
        QualityReport with all issues found.
    """
    if isinstance(card, str):
        try:
            card = json.loads(card)
        except json.JSONDecodeError:
            report = QualityReport(card_type="economy", ticker="ECONOMY")
            report.issues.append(QualityIssue(
                rule="PARSE_FAIL",
                severity="critical",
                field="root",
                message="Card is not valid JSON."
            ))
            return report

    if not isinstance(card, dict):
        report = QualityReport(card_type="economy", ticker="ECONOMY")
        report.issues.append(QualityIssue(
            rule="PARSE_FAIL",
            severity="critical",
            field="root",
            message=f"Card parsed to {type(card).__name__}, expected dict."
        ))
        return report

    report = QualityReport(card_type="economy", ticker="ECONOMY")

    # 1. Schema completeness
    _check_schema(card, ECONOMY_REQUIRED_SCHEMA, report)

    # 2. Placeholder detection
    _check_placeholder_text(card, report)

    # 3. todaysAction quality
    _check_todays_action_quality(card, report, "economy")

    # 4. Economy-specific bias
    _check_economy_bias(card, report)

    # 5. Sector rotation
    _check_economy_sectors(card, report)

    # 6. Content substance
    _check_substance(card, ECONOMY_SUBSTANTIVE_FIELDS, report)

    return report
