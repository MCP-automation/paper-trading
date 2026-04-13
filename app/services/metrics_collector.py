import time
from datetime import datetime
from collections import deque
from typing import Dict, Any, Optional

class MetricsCollector:
    """
    Tracks system performance metrics to verify the effectiveness 
    of caching, deduplication, and rate limiting.
    """
    def __init__(self):
        self.start_time = time.time()
        
        # API Call Stats
        self.total_api_calls = 0
        self.api_call_timestamps = deque(maxlen=1000) # For CPS calculation
        self.last_api_call = None
        
        # Cache & Dedup Stats
        self.cache_hits = 0
        self.cache_misses = 0
        self.dedup_prevented = 0
        self.errors_429 = 0
        
        # Strategy Stats
        # strategy_name -> {exec_time_ms, last_update}
        self.strategy_metrics: Dict[str, Dict[str, Any]] = {}

    # --- API Call Tracking ---
    def record_api_call(self):
        now = time.time()
        self.total_api_calls += 1
        self.api_call_timestamps.append(now)
        self.last_api_call = datetime.fromtimestamp(now).isoformat() + "Z"

    def record_429(self):
        self.errors_429 += 1

    # --- Efficiency Tracking ---
    def record_cache_hit(self):
        self.cache_hits += 1

    def record_cache_miss(self):
        self.cache_misses += 1

    def record_dedup(self):
        self.dedup_prevented += 1

    # --- Strategy Tracking ---
    def record_strategy_exec(self, name: str, duration_ms: float):
        self.strategy_metrics[name] = {
            "exec_time_ms": round(duration_ms, 2),
            "last_update": datetime.utcnow().isoformat() + "Z"
        }

    # --- Calculation Helpers ---
    def get_api_calls_per_second(self) -> float:
        now = time.time()
        # Look at last 10 seconds of activity
        window = 10
        recent_calls = [t for t in self.api_call_timestamps if t > now - window]
        return round(len(recent_calls) / window, 2)

    def get_cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        if total == 0: return 0.0
        return round(self.cache_hits / total, 2)

    def get_system_metrics(self) -> Dict[str, Any]:
        uptime = time.time() - self.start_time
        
        # Determine status
        status = "healthy"
        if self.errors_429 > 0:
            status = "degraded (429s detected)"
        if self.get_api_calls_per_second() > 5:
            status = "warning (high traffic)"

        return {
            "api_calls_per_second": self.get_api_calls_per_second(),
            "cache_hit_rate": self.get_cache_hit_rate(),
            "dedup_prevented": self.dedup_prevented,
            "errors_429": self.errors_429,
            "strategies": self.strategy_metrics,
            "total_api_calls": self.total_api_calls,
            "last_api_call": self.last_api_call,
            "uptime_seconds": int(uptime),
            "status": status,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

# Global singleton instance
metrics = MetricsCollector()
