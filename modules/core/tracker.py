import time
import threading
import json
from dataclasses import dataclass, field
from typing import List, Dict, Any

@dataclass
class ExecutionMetrics:
    total_calls: int = 0
    total_tokens: int = 0
    success_count: int = 0
    failure_count: int = 0
    retry_count: int = 0          # Intermediate retries (429, 500, timeout)
    details: List[str] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0
    errors: List[str] = field(default_factory=list)
    artifacts: Dict[str, str] = field(default_factory=dict)
    # Per-ticker outcomes: ticker -> {status, model, tokens, error, retries, quality_issues}
    ticker_outcomes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Per-ticker quality reports: ticker -> list of {rule, severity, field, message}
    quality_reports: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)
    # Per-ticker data-accuracy reports: ticker -> list of {rule, severity, field, message}
    data_reports: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)

class ExecutionTracker:
    """
    Tracks the execution of a pipeline run, including token usage, API calls,
    retries, quality reports, and per-ticker outcomes.
    Designed for rich Discord reporting.
    """
    def __init__(self):
        self.metrics = ExecutionMetrics()
        self.action_type = "Unknown"
        self.custom_results = {}
        self._lock = threading.Lock()

    def start(self, action_type: str = "Unknown"):
        self.action_type = action_type
        self.metrics.start_time = time.time()
        self.metrics.total_calls = 0
        self.metrics.total_tokens = 0
        self.metrics.success_count = 0
        self.metrics.failure_count = 0
        self.metrics.retry_count = 0
        self.metrics.details = []
        self.metrics.errors = []
        self.metrics.artifacts = {}
        self.metrics.ticker_outcomes = {}
        self.metrics.quality_reports = {}
        self.metrics.data_reports = {}
        self.custom_results = {}

    def set_result(self, key: str, value: str):
        """Sets a custom result field to be displayed on the dashboard."""
        with self._lock:
            self.custom_results[key] = value

    def log_call(self, tokens: int, success: bool, model: str, ticker: str = None, error: str = None):
        with self._lock:
            self.metrics.total_calls += 1
            self.metrics.total_tokens += tokens
            if success:
                self.metrics.success_count += 1
                if ticker:
                    self.metrics.details.append(f"✅ {ticker}: Success ({model}, {tokens} tokens)")
                    # Record per-ticker outcome
                    outcome = self.metrics.ticker_outcomes.get(ticker, {})
                    outcome['status'] = 'success'
                    outcome['model'] = model
                    outcome['tokens'] = outcome.get('tokens', 0) + tokens
                    self.metrics.ticker_outcomes[ticker] = outcome
            else:
                self.metrics.failure_count += 1
                err_msg = error or "Unknown Error"
                self.metrics.errors.append(f"❌ {ticker or 'Global'}: {err_msg}")
                if ticker:
                    self.metrics.details.append(f"❌ {ticker}: Failed ({model})")
                    # Record per-ticker outcome
                    outcome = self.metrics.ticker_outcomes.get(ticker, {})
                    outcome['status'] = 'failed'
                    outcome['model'] = model
                    outcome['error'] = err_msg
                    self.metrics.ticker_outcomes[ticker] = outcome

    def log_retry(self, model: str, ticker: str = None, reason: str = ""):
        """Logs an intermediate retry attempt (429, 500, timeout, etc.)."""
        with self._lock:
            self.metrics.retry_count += 1
            if ticker:
                outcome = self.metrics.ticker_outcomes.get(ticker, {})
                outcome['retries'] = outcome.get('retries', 0) + 1
                self.metrics.ticker_outcomes[ticker] = outcome

    def log_error(self, ticker: str, error: str):
        """Logs a non-API failure (e.g., missing data) without incrementing API call count."""
        with self._lock:
            self.metrics.failure_count += 1
            self.metrics.errors.append(f"❌ {ticker}: {error}")
            self.metrics.details.append(f"❌ {ticker}: {error}")

    def log_quality(self, ticker: str, quality_report):
        """
        Stores quality validation results for a ticker.
        
        Args:
            ticker: The ticker symbol (e.g., 'AAPL' or 'ECONOMY')
            quality_report: A QualityReport object with .issues, .passed, etc.
        """
        with self._lock:
            issues = []
            for issue in quality_report.issues:
                issues.append({
                    'rule': issue.rule,
                    'severity': issue.severity,
                    'field': issue.field,
                    'message': issue.message
                })
            self.metrics.quality_reports[ticker] = issues
            
            # Update ticker outcome with quality status
            outcome = self.metrics.ticker_outcomes.get(ticker, {})
            if not quality_report.passed:
                outcome['quality'] = 'fail'
                outcome['quality_critical'] = quality_report.critical_count
                outcome['quality_warnings'] = quality_report.warning_count
            elif quality_report.warning_count > 0:
                outcome['quality'] = 'warnings'
                outcome['quality_warnings'] = quality_report.warning_count
            else:
                outcome['quality'] = 'perfect'
            self.metrics.ticker_outcomes[ticker] = outcome

    def log_data_accuracy(self, ticker: str, data_report):
        """
        Stores data-accuracy validation results for a ticker.
        
        Args:
            ticker: The ticker symbol (e.g., 'AAPL' or 'ECONOMY')
            data_report: A DataReport object with .issues, .passed, etc.
        """
        with self._lock:
            issues = []
            for issue in data_report.issues:
                issues.append({
                    'rule': issue.rule,
                    'severity': issue.severity,
                    'field': issue.field,
                    'message': issue.message
                })
            self.metrics.data_reports[ticker] = issues
            
            # Update ticker outcome with data accuracy status
            outcome = self.metrics.ticker_outcomes.get(ticker, {})
            if not data_report.passed:
                outcome['data_accuracy'] = 'fail'
                outcome['data_critical'] = data_report.critical_count
                outcome['data_warnings'] = data_report.warning_count
            elif data_report.warning_count > 0:
                outcome['data_accuracy'] = 'warnings'
                outcome['data_warnings'] = data_report.warning_count
            else:
                outcome['data_accuracy'] = 'perfect'
            self.metrics.ticker_outcomes[ticker] = outcome

    def register_artifact(self, name: str, content: str):
        """Registers a generated card (JSON) to be attached to the report."""
        with self._lock:
            self.metrics.artifacts[name] = content

    def finish(self):
        self.metrics.end_time = time.time()

    def get_summary(self):
        duration = self.metrics.end_time - self.metrics.start_time
        return {
            "total_calls": self.metrics.total_calls,
            "total_tokens": self.metrics.total_tokens,
            "retry_count": self.metrics.retry_count,
            "success_rate": f"{(self.metrics.success_count / self.metrics.total_calls * 100):.1f}%" if self.metrics.total_calls > 0 else "0%",
            "duration": f"{duration:.1f}s",
            "details": self.metrics.details,
            "errors": self.metrics.errors,
            "artifacts_count": len(self.metrics.artifacts)
        }

    def _build_ai_embed(self, target_date: str, summary: dict, color: int) -> dict:
        """Build the main embed for AI pipeline actions."""
        embed = {
            "title": f"🏦 Analyst Workbench | {target_date}",
            "description": f"Action: **{self.action_type.replace('_', ' ')}**",
            "color": color,
            "fields": [],
            "footer": {"text": "Analyst Workbench v2.5 | Macro Intel Engine"},
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        }

        outcomes = self.metrics.ticker_outcomes
        total_tickers = len(outcomes) if outcomes else 0
        
        # Categorize tickers
        updated = []      # success + quality perfect or warnings-only
        quality_fail = []  # success but quality critical
        failed = []        # API/pipeline failure
        
        for ticker, info in outcomes.items():
            status = info.get('status', 'unknown')
            quality = info.get('quality', 'unknown')
            if status == 'failed':
                failed.append(ticker)
            elif quality == 'fail':
                quality_fail.append(ticker)
            else:
                updated.append(ticker)

        updated_count = len(updated)
        
        # --- ROW 1: Key Stats ---
        total_http = summary['total_calls'] + summary['retry_count']
        if total_tickers > 0:
            embed["fields"].append({
                "name": "📊 Tickers", 
                "value": f"**{updated_count}/{total_tickers}** Updated", 
                "inline": True
            })
        
        # API Calls: succeeded vs total attempts (calls + retries)
        succeeded = self.metrics.success_count
        total_attempts = summary['total_calls'] + summary['retry_count']
        
        if total_attempts > 0:
            call_detail = f"**{succeeded}** succeeded\n**{total_attempts}** attempts"
            if summary['retry_count'] > 0:
                call_detail += f"\n🔁 {summary['retry_count']} retries"
        else:
            call_detail = "**0** attempts"
        
        embed["fields"].append({"name": "🔄 API Calls", "value": call_detail, "inline": True})
        embed["fields"].append({"name": "🪙 Tokens", "value": f"**{summary['total_tokens']:,}**", "inline": True})
        embed["fields"].append({"name": "⏱️ Duration", "value": f"**{summary['duration']}**", "inline": True})
        
        if summary['artifacts_count'] > 0:
            embed["fields"].append({"name": "📁 Files", "value": f"**{summary['artifacts_count'] + 1}**", "inline": True})
        
        # Blank field for row alignment
        if len(embed["fields"]) % 3 != 0:
            embed["fields"].append({"name": "\u200b", "value": "\u200b", "inline": True})

        # --- SECTION: ✅ Updated Successfully ---
        if updated:
            # Group by quality status
            perfect = [t for t in updated if outcomes[t].get('quality') == 'perfect']
            with_warnings = [t for t in updated if outcomes[t].get('quality') == 'warnings']
            no_quality = [t for t in updated if outcomes[t].get('quality') not in ('perfect', 'warnings', 'fail')]

            lines = []
            for t in perfect:
                lines.append(f"✅ **{t}**")
            for t in with_warnings:
                wc = outcomes[t].get('quality_warnings', 0)
                lines.append(f"✅ **{t}** ⚠️ {wc} warning{'s' if wc != 1 else ''}")
                # Show warning details so user can evaluate them
                issues = self.metrics.quality_reports.get(t, [])
                for issue in issues:
                    msg = issue['message']
                    if len(msg) > 120:
                        msg = msg[:117] + "..."
                    lines.append(f"   🟡 `{issue['rule']}` → {msg}")
            for t in no_quality:
                lines.append(f"✅ **{t}**")
            
            if lines:
                text = "\n".join(lines)
                if len(text) > 1024:
                    text = text[:1021] + "..."
                embed["fields"].append({
                    "name": f"✅ Updated ({len(updated)})",
                    "value": text,
                    "inline": False
                })

        # --- SECTION: 🔴 Quality Failures (critical issues with details) ---
        if quality_fail:
            lines = []
            for ticker in quality_fail:
                cc = outcomes[ticker].get('quality_critical', 0)
                wc = outcomes[ticker].get('quality_warnings', 0)
                lines.append(f"⚠️ **{ticker}** — {cc} critical, {wc} warning{'s' if wc != 1 else ''}")
                
                # Add specific issue details
                issues = self.metrics.quality_reports.get(ticker, [])
                for issue in issues:
                    if issue['severity'] == 'critical':
                        # Truncate long messages
                        msg = issue['message']
                        if len(msg) > 120:
                            msg = msg[:117] + "..."
                        lines.append(f"   🔴 `{issue['rule']}` → {msg}")
                    elif issue['severity'] == 'warning':
                        msg = issue['message']
                        if len(msg) > 120:
                            msg = msg[:117] + "..."
                        lines.append(f"   🟡 `{issue['rule']}` → {msg}")
            
            text = "\n".join(lines)
            if len(text) > 1024:
                text = text[:1021] + "..."
            embed["fields"].append({
                "name": f"🔴 Quality Issues ({len(quality_fail)})",
                "value": text,
                "inline": False
            })

        # --- SECTION: 📊 Data Accuracy Issues ---
        data_issue_tickers = [
            t for t, info in outcomes.items()
            if info.get('data_accuracy') in ('fail', 'warnings')
        ]
        if data_issue_tickers:
            lines = []
            for ticker in data_issue_tickers:
                dc = outcomes[ticker].get('data_critical', 0)
                dw = outcomes[ticker].get('data_warnings', 0)
                issue_count = dc + dw
                lines.append(f"🔴 **{ticker}** — {issue_count} data issue{'s' if issue_count != 1 else ''}")
                
                data_issues = self.metrics.data_reports.get(ticker, [])
                for issue in data_issues:
                    msg = issue['message']
                    if len(msg) > 120:
                        msg = msg[:117] + "..."
                    lines.append(f"   🔴 `{issue['rule']}` → {msg}")
            
            text = "\n".join(lines)
            if len(text) > 1024:
                text = text[:1021] + "..."
            embed["fields"].append({
                "name": f"📊 Data Accuracy Issues ({len(data_issue_tickers)})",
                "value": text,
                "inline": False
            })

        # --- SECTION: ❌ Failed ---
        if failed:
            lines = []
            for ticker in failed:
                err = outcomes[ticker].get('error', 'Unknown')
                retries = outcomes[ticker].get('retries', 0)
                detail = f"❌ **{ticker}** — {err}"
                if retries > 0:
                    detail += f" (after {retries} retries)"
                lines.append(detail)
            
            text = "\n".join(lines)
            if len(text) > 1024:
                text = text[:1021] + "..."
            embed["fields"].append({
                "name": f"❌ Failed ({len(failed)})",
                "value": text,
                "inline": False
            })
        
        # --- SECTION: 🌍 Macro Narrative (Economy Card) ---
        if "ECONOMY_CARD" in self.metrics.artifacts:
            try:
                eco_data = json.loads(self.metrics.artifacts["ECONOMY_CARD"])
                narrative = eco_data.get("marketNarrative", "No narrative found.")
                if len(narrative) > 500:
                    narrative = narrative[:497] + "..."
                embed["fields"].append({
                    "name": "🌍 Macro State (Preview)", 
                    "value": f"```\n{narrative}\n```", 
                    "inline": False
                })
            except:
                pass

        return embed

    def _build_data_embed(self, target_date: str, summary: dict, color: int) -> dict:
        """Build the embed for non-AI actions (News, Inspect, etc.)."""
        embed = {
            "title": f"🏦 Analyst Workbench | {target_date}",
            "description": f"Action: **{self.action_type.replace('_', ' ')}**",
            "color": color,
            "fields": [],
            "footer": {"text": "Analyst Workbench v2.5 | Macro Intel Engine"},
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        }

        if self.action_type == "News_Check":
            news_status = self.custom_results.get("news_status", "Unknown")
            embed["fields"].append({"name": "📰 News Status", "value": f"**{news_status}**", "inline": True})
        elif self.action_type == "DB_Inspection":
            embed["fields"].append({"name": "📰 News", "value": f"**{self.custom_results.get('market_news', 'Unknown')}**", "inline": True})
            embed["fields"].append({"name": "🌎 Economy", "value": f"**{self.custom_results.get('economy_card', 'Unknown')}**", "inline": True})
            embed["fields"].append({"name": "📦 Tickers", "value": f"**{self.custom_results.get('updated_tickers', 'Unknown')}**", "inline": True})
            embed["fields"].append({"name": "📊 Price Data", "value": f"`{self.custom_results.get('market_data_rows', '0')} rows`", "inline": True})
        
        excluded_keys = ["news_status", "market_news", "economy_card", "updated_tickers", "market_data_rows"]
        for k, v in self.custom_results.items():
            if k in excluded_keys: continue
            embed["fields"].append({"name": k.replace("_", " ").title(), "value": f"`{v}`", "inline": True})

        embed["fields"].append({"name": "⏱️ Duration", "value": f"**{summary['duration']}**", "inline": True})
        if summary['artifacts_count'] > 0:
            embed["fields"].append({"name": "📁 Files", "value": f"**{summary['artifacts_count'] + 1}**", "inline": True})

        if summary["details"]:
            details_text = "\n".join(summary["details"])
            if len(details_text) > 1024:
                details_text = details_text[:1021] + "..."
            embed["fields"].append({"name": "📝 Details", "value": details_text, "inline": False})

        if summary["errors"]:
            error_text = "\n".join(summary["errors"])
            if len(error_text) > 1024:
                error_text = error_text[:1021] + "..."
            embed["fields"].append({"name": "⚠️ Failures", "value": error_text, "inline": False})
        
        return embed

    def get_discord_embeds(self, target_date: str):
        summary = self.get_summary()
        
        # Determine color based on outcomes
        outcomes = self.metrics.ticker_outcomes
        if outcomes:
            all_ok = all(
                info.get('status') == 'success' and info.get('quality', 'perfect') != 'fail' 
                for info in outcomes.values()
            )
            all_bad = all(
                info.get('status') == 'failed' 
                for info in outcomes.values()
            )
            if all_ok:
                color = 0x2ecc71  # Green
            elif all_bad:
                color = 0xe74c3c  # Red
            else:
                color = 0xf1c40f  # Yellow
        else:
            # Fallback for non-ticker actions
            if summary["success_rate"] == "100.0%":
                color = 0x2ecc71
            elif summary["success_rate"] == "0%":
                color = 0xe74c3c
            else:
                color = 0xf1c40f

        ai_actions = ["Full_Pipeline_Run", "Economy_Card_Update", "Company_Card_Update"]
        
        if self.action_type in ai_actions:
            embed = self._build_ai_embed(target_date, summary, color)
        else:
            embed = self._build_data_embed(target_date, summary, color)

        return [embed]
