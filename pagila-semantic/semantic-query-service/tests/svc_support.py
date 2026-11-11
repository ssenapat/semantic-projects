import threading

KEY_A = "alpha-key-0123456789"
KEY_B = "bravo-key-0123456789"


class FakeExecutor:
    """Stands in for PostgreSQL. Records what it was asked to run."""

    def __init__(self):
        self.calls = []
        self.fail = None          # exception instance to raise
        self.gate = None          # threading.Event to wait on
        self.entered = threading.Event()

    def run(self, sql, params):
        self.calls.append((sql, params))
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail is not None:
            raise self.fail
        if "MAX(" in sql:
            return ["max"], [["2026-07-28"]]
        return ["total_revenue"], [[170962.39]]


REVENUE = {"model": "payment_activity", "measures": ["total_revenue"]}
