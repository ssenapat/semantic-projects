"""Process-local metrics rendered as `name value` text lines (CONTRACT 6.9)."""
from __future__ import annotations

import threading


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests_total = 0
        self.low_confidence_total = 0
        self.retrieval_latency_ms = 0.0       # last observed
        self.context_tokens_estimate = 0      # last observed

    def request(self) -> None:
        with self._lock:
            self.requests_total += 1

    def retrieval(self, latency_ms: float, tokens: int, low_confidence: bool) -> None:
        with self._lock:
            self.retrieval_latency_ms = latency_ms
            self.context_tokens_estimate = tokens
            if low_confidence:
                self.low_confidence_total += 1

    def render(self) -> str:
        with self._lock:
            return (f"semantic_retriever_requests_total {self.requests_total}\n"
                    f"semantic_retriever_retrieval_latency_ms {self.retrieval_latency_ms:.3f}\n"
                    f"semantic_retriever_context_tokens_estimate {self.context_tokens_estimate}\n"
                    f"semantic_retriever_low_confidence_total {self.low_confidence_total}\n")
