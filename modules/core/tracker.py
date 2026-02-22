import time
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

    def start(self):
        self.metrics.start_time = time.time()
        self.metrics.total_calls = 0
        self.metrics.total_tokens = 0
        self.metrics.success_count = 0
        self.metrics.failure_count = 0
        self.metrics.details = []
        self.metrics.errors = []
        self.metrics.artifacts = {}

    def log_call(self, tokens: int, success: bool, model: str, ticker: str = None, error: str = None):
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

    def register_artifact(self, name: str, content: str):
        """Registers a generated card (JSON) to be attached to the report."""
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
            "description": "The AI analysis pipeline has completed. Actionable cards are attached below.",
            "color": color,
            "fields": [
                {"name": "🤖 API Calls", "value": f"`{summary['total_calls']}`", "inline": True},
                {"name": "🪙 Tokens", "value": f"`{summary['total_tokens']:,}`", "inline": True},
                {"name": "📈 Status", "value": f"`{summary['success_rate']}`", "inline": True},
                {"name": "🕒 Duration", "value": f"`{summary['duration']}`", "inline": True},
                {"name": "📁 Files", "value": f"`{summary['artifacts_count'] + 1}`", "inline": True} # +1 for Log
            ],
            "footer": {"text": "Analyst Workbench v2.5 | Macro Intel Engine"},
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        }

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
