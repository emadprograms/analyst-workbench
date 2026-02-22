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
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    errors: List[str] = field(default_factory=list)

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
            "errors": self.metrics.errors
        }

    def get_discord_embeds(self, target_date: str):
        summary = self.get_summary()
        embed = {
            "title": f"📊 Execution Dashboard: {target_date}",
            "description": f"Analyst Workbench pipeline completed for the logical session.",
            "color": 3066993 if summary["success_rate"] == "100.0%" else 15844367, # 0x2ecc71 or 0xf1c40f
            "fields": [
                {"name": "🕒 Duration", "value": summary["duration"], "inline": True},
                {"name": "🤖 API Calls", "value": str(summary["total_calls"]), "inline": True},
                {"name": "🪙 Token Usage", "value": f"{summary['total_tokens']:,}", "inline": True},
                {"name": "✅ Success Rate", "value": summary["success_rate"], "inline": True}
            ],
            "footer": {"text": "Model Tracking Active"},
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        }

        if summary["details"]:
            details_text = "\n".join(summary["details"])[:1024]
            embed["fields"].append({"name": "📝 Execution Log", "value": details_text, "inline": False})
            
        if summary["errors"]:
            error_text = "\n".join(summary["errors"])[:1024]
            embed["fields"].append({"name": "⚠️ Failures", "value": error_text, "inline": False})
            
        return [embed]
