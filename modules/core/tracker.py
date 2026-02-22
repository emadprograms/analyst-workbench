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
