"""In-process metrics rendered as `name value` lines (CONTRACT 6.9)."""
import threading

COUNTERS = (
    "intent_parser_requests_total", "intent_parser_failures_total",
    "intent_parser_invalid_output_total", "intent_parser_model_retry_total",
    "intent_parser_clarification_total", "intent_parser_unsupported_total",
    "intent_parser_sql_leakage_rejected_total",
)
GAUGES = ("intent_parser_latency_ms", "intent_parser_llm_latency_ms")


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, float] = {n: 0 for n in COUNTERS + GAUGES}

    def inc(self, name: str, amount: float = 1) -> None:
        with self._lock:
            self._values[name] += amount

    def set(self, name: str, value: float) -> None:
        with self._lock:
            self._values[name] = round(value, 3)

    def get(self, name: str) -> float:
        return self._values[name]

    def render(self) -> str:
        with self._lock:
            return "".join(f"{n} {self._values[n]}\n" for n in COUNTERS + GAUGES)
