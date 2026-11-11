import json
import logging

import pytest

from semantic_layer import SemanticLayer

SENTINEL = "FILTERVALUE_SENTINEL_42"


class FakeExecutor:
    def __init__(self, cols, rows):
        self.cols, self.rows, self.calls = cols, rows, []

    def run(self, sql, params):
        self.calls.append((sql, params))
        if "MAX(r.rental_date)" in sql:
            return ["max"], [["2025-06-30 12:00:00"]]
        return self.cols, self.rows


def audit_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "semantic_layer.audit"]


def test_success_redacts_scalar_and_list_filter_values_without_mutating_request(caplog):
    caplog.set_level(logging.INFO, logger="semantic_layer.audit")
    layer = SemanticLayer(executor=FakeExecutor(["rating", "rental_count"], [["PG", 1]]))
    request = {"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["rating"],
               "filters": [{"dimension": "rating", "op": "eq", "value": SENTINEL},
                           {"dimension": "rating", "op": "in", "value": [SENTINEL, SENTINEL + "_B"]}]}
    snapshot = json.loads(json.dumps(request))
    layer.query(request, caller="tester")
    assert request == snapshot
    (line,) = audit_lines(caplog)
    assert "FILTERVALUE_SENTINEL" not in line
    entry = json.loads(line)
    assert entry["request"]["filters"] == [{"dimension": "rating", "op": "eq", "value": "<redacted>"},
                                           {"dimension": "rating", "op": "in", "value": "<redacted>"}]
    assert entry["request"]["model"] == "rental_activity"
    assert "rows" not in entry and "params" not in entry


def test_refused_request_with_filter_logs_one_line_without_sentinel(caplog):
    caplog.set_level(logging.INFO, logger="semantic_layer.audit")
    layer = SemanticLayer(executor=FakeExecutor([], []))
    with pytest.raises(Exception):
        layer.query({"model": "payment_activity", "measures": ["total_revenue", "rental_count"],
                     "filters": [{"dimension": "rating", "op": "eq", "value": SENTINEL}]}, caller="tester")
    (line,) = audit_lines(caplog)
    assert SENTINEL not in line
    entry = json.loads(line)
    assert entry["error"] and entry["request"]["filters"][0]["value"] == "<redacted>"


@pytest.mark.parametrize("bad", [None, "text", 42, ["x"], {"model": "m", "filters": "oops"},
                                 {"model": "m", "filters": [None, "s", 3, {"dimension": "d"}]}])
def test_malformed_requests_still_produce_one_audit_line(caplog, bad):
    caplog.set_level(logging.INFO, logger="semantic_layer.audit")
    layer = SemanticLayer(executor=FakeExecutor([], []))
    with pytest.raises(Exception):
        layer.query(bad, caller="tester")
    assert len(audit_lines(caplog)) == 1


@pytest.mark.parametrize("flt", [
    {"dimension": "rental_date", "op": "eq", "value": SENTINEL},
    {"dimension": "rental_date", "op": "between", "value": ["2025-01-01", SENTINEL]},
])
def test_bad_date_filter_value_is_not_echoed_in_error_or_audit(caplog, flt):
    caplog.set_level(logging.INFO, logger="semantic_layer.audit")
    layer = SemanticLayer(executor=FakeExecutor([], []))
    with pytest.raises(Exception) as exc:
        layer.query({"model": "rental_activity", "measures": ["rental_count"], "filters": [flt]}, caller="tester")
    assert "YYYY-MM-DD" in str(exc.value)
    assert SENTINEL not in str(exc.value)
    (line,) = audit_lines(caplog)
    assert SENTINEL not in line
    assert "YYYY-MM-DD" in json.loads(line)["error"]
