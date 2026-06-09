import time
from collections import deque
from dataclasses import dataclass
from typing import Dict, List


@dataclass
class RequestRecord:
    timestamp: float       # time.monotonic()
    route_id: str
    status_code: int
    total_latency_ms: float
    upstream_latency_ms: float
    rate_limited: bool
    circuit_open: bool


def _percentile(sorted_values: List[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(int(p * len(sorted_values)), len(sorted_values) - 1)
    return sorted_values[idx]


class AnalyticsCollector:
    def __init__(self, window_seconds: int):
        self.window_seconds = window_seconds
        self._records: deque = deque()

    def record(self, rec: RequestRecord) -> None:
        self._records.append(rec)
        self._prune()

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.window_seconds
        while self._records and self._records[0].timestamp < cutoff:
            self._records.popleft()

    def get_summary(self) -> dict:
        self._prune()
        records = list(self._records)
        total = len(records)

        if total == 0:
            return {
                "window_seconds": self.window_seconds,
                "total_requests": 0,
                "requests_per_second": 0.0,
                "error_rate_percent": 0.0,
                "rate_limit_rate_percent": 0.0,
                "circuit_open_rate_percent": 0.0,
                "latency_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0, "avg": 0.0},
                "per_route": {},
            }

        errors = sum(1 for r in records if r.status_code >= 500)
        rate_limited = sum(1 for r in records if r.rate_limited)
        circuit_open = sum(1 for r in records if r.circuit_open)

        latencies = sorted(r.total_latency_ms for r in records)
        avg_latency = sum(latencies) / total

        per_route: Dict[str, dict] = {}
        for r in records:
            entry = per_route.setdefault(
                r.route_id,
                {"_total": 0, "_errors": 0, "_latency_sum": 0.0},
            )
            entry["_total"] += 1
            if r.status_code >= 500:
                entry["_errors"] += 1
            entry["_latency_sum"] += r.total_latency_ms

        route_summary = {}
        for route_id, entry in per_route.items():
            t = entry["_total"]
            route_summary[route_id] = {
                "total_requests": t,
                "requests_per_second": round(t / self.window_seconds, 4),
                "error_rate_percent": round(entry["_errors"] / t * 100, 2),
                "avg_latency_ms": round(entry["_latency_sum"] / t, 3),
            }

        return {
            "window_seconds": self.window_seconds,
            "total_requests": total,
            "requests_per_second": round(total / self.window_seconds, 4),
            "error_rate_percent": round(errors / total * 100, 2),
            "rate_limit_rate_percent": round(rate_limited / total * 100, 2),
            "circuit_open_rate_percent": round(circuit_open / total * 100, 2),
            "latency_ms": {
                "p50": round(_percentile(latencies, 0.50), 3),
                "p95": round(_percentile(latencies, 0.95), 3),
                "p99": round(_percentile(latencies, 0.99), 3),
                "avg": round(avg_latency, 3),
            },
            "per_route": route_summary,
        }
