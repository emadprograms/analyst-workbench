import time
import threading
from dataclasses import dataclass, field
from typing import List, Dict

@dataclass
class ExecutionMetrics:
    total_calls: int = 0
    total_tokens: int = 0
    success_count: int = 0
    failure_count: int = 0
    details: List[str] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0
    errors: List[str] = field(default_factory=list)
    artifacts: Dict[str, str] = field(default_factory=dict)

class ExecutionTracker:
    """
    Tracks the execution of a pipeline run, including token usage and API calls.
    Designed for use with Discord reporting.
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
        self.metrics.details = []
        self.metrics.errors = []
        self.metrics.artifacts = {}
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
            else:
                self.metrics.failure_count += 1
                err_msg = error or "Unknown Error"
                self.metrics.errors.append(f"❌ {ticker or 'Global'}: {err_msg}")
                if ticker:
                    self.metrics.details.append(f"❌ {ticker}: Failed ({model})")

    def log_error(self, ticker: str, error: str):
        """Logs a non-API failure (e.g., missing data) without incrementing API call count."""
        with self._lock:
            self.metrics.failure_count += 1
            self.metrics.errors.append(f"❌ {ticker}: {error}")
            self.metrics.details.append(f"❌ {ticker}: {error}")

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
            "success_rate": f"{(self.metrics.success_count / self.metrics.total_calls * 100):.1f}%" if self.metrics.total_calls > 0 else "0%",
            "duration": f"{duration:.1f}s",
            "details": self.metrics.details,
            "errors": self.metrics.errors,
            "artifacts_count": len(self.metrics.artifacts)
        }

    def get_discord_embeds(self, target_date: str):
        summary = self.get_summary()
        
        # Determine color based on success
        if summary["success_rate"] == "100.0%":
            color = 0x2ecc71 # Green
        elif summary["success_rate"] == "0.0%":
            color = 0xe74c3c # Red
        else:
            color = 0xf1c40f # Yellow

        embed = {
            "title": f"🏦 Analyst Workbench | {target_date}",
            "description": f"Action: **{self.action_type.replace('_', ' ')}**",
            "color": color,
            "fields": [],
            "footer": {"text": "Analyst Workbench v2.5 | Macro Intel Engine"},
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        }

        # Categories for layout
        ai_actions = ["Full_Pipeline_Run", "Economy_Card_Update", "Company_Card_Update"]
        
        if self.action_type in ai_actions:
            # Full AI Dashboard
            embed["fields"].append({"name": "🤖 API Calls", "value": f"`{summary['total_calls']}`", "inline": True})
            embed["fields"].append({"name": "🪙 Tokens", "value": f"`{summary['total_tokens']:,}`", "inline": True})
            embed["fields"].append({"name": "📈 Status", "value": f"`{summary['success_rate']}`", "inline": True})
            embed["fields"].append({"name": "🕒 Duration", "value": f"`{summary['duration']}`", "inline": True})
            if summary['artifacts_count'] > 0:
                embed["fields"].append({"name": "📁 Files", "value": f"`{summary['artifacts_count'] + 1}`", "inline": True})
        else:
            # Simplified Data Dashboard (Input News, Check News, Inspect, etc.)
            if self.action_type == "News_Check":
                news_status = self.custom_results.get("news_status", "Unknown")
                embed["fields"].append({"name": "📰 News Status", "value": f"**{news_status}**", "inline": True})
            elif self.action_type == "DB_Inspection":
                embed["fields"].append({"name": "📰 News", "value": f"**{self.custom_results.get('market_news', 'Unknown')}**", "inline": True})
                embed["fields"].append({"name": "🌎 Economy", "value": f"**{self.custom_results.get('economy_card', 'Unknown')}**", "inline": True})
                embed["fields"].append({"name": "📦 Tickers", "value": f"**{self.custom_results.get('updated_tickers', 'Unknown')}**", "inline": True})
                embed["fields"].append({"name": "📊 Price Data", "value": f"`{self.custom_results.get('market_data_rows', '0')} rows`", "inline": True})
            
            # Show any other custom results
            excluded_keys = ["news_status", "market_news", "economy_card", "updated_tickers", "market_data_rows"]
            for k, v in self.custom_results.items():
                if k in excluded_keys: continue
                embed["fields"].append({"name": k.replace("_", " ").title(), "value": f"`{v}`", "inline": True})

            embed["fields"].append({"name": "🕒 Duration", "value": f"`{summary['duration']}`", "inline": True})
            if summary['artifacts_count'] > 0:
                embed["fields"].append({"name": "📁 Files", "value": f"`{summary['artifacts_count'] + 1}`", "inline": True})

        # Enhanced: Include Macro Narrative if Economy Card was generated
        if "ECONOMY_CARD" in self.metrics.artifacts:
            try:
                import json
                eco_data = json.loads(self.metrics.artifacts["ECONOMY_CARD"])
                narrative = eco_data.get("marketNarrative", "No narrative found.")
                # Truncate if too long
                if len(narrative) > 500:
                    narrative = narrative[:497] + "..."
                embed["fields"].append({"name": "🌍 Macro State (Preview)", "value": f"```\n{narrative}\n```", "inline": False})
            except:
                pass

        if summary["details"]:
            details_text = "\n".join(summary["details"])
            if len(details_text) > 1024:
                details_text = details_text[:1021] + "..."
            embed["fields"].append({"name": "📝 Execution Log", "value": details_text, "inline": False})
            
        if summary["errors"]:
            error_text = "\n".join(summary["errors"])
            if len(error_text) > 1024:
                error_text = error_text[:1021] + "..."
            embed["fields"].append({"name": "⚠️ Failures", "value": error_text, "inline": False})
            
        return [embed]
