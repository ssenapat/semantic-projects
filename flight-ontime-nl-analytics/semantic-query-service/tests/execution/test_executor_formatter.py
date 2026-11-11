from datetime import date
from decimal import Decimal

import pytest

from semantic_query_service.app.schemas.errors import (DatabaseUnavailable, QueryExecutionFailed, QueryTimeout,
                                                       SqlGuardRejection)
from semantic_query_service.app.schemas.result import ResultColumn
from semantic_query_service.app.services import result_formatter as rf
from semantic_query_service.app.services.reference_date import resolve_reference_date
from semantic_query_service.app.services.explain_guard import check_cost


def test_executor_fetch_and_password_from_pgpassword_only(executor, db):
    rows = executor.fetch("SELECT 1 AS flight_count FROM ontime.flight LIMIT 1")
    assert rows == [{"flight_count": 42}]
    assert db.passwords == ["s3cret-pw"]


def test_executor_guards_before_connecting(executor, db):
    with pytest.raises(SqlGuardRejection):
        executor.fetch("DELETE FROM ontime.flight")
    assert db.statements == [] and db.passwords == []


def test_executor_no_password_is_unavailable(settings, db):
    from semantic_query_service.app.services.postgres_executor import PostgresExecutor
    settings.pgpassword = None
    with pytest.raises(DatabaseUnavailable):
        PostgresExecutor(settings, connect=db.connect).fetch("SELECT 1 FROM ontime.flight LIMIT 1")


def test_executor_error_mapping(executor, db):
    class QueryCanceled(Exception):
        pass
    sql = "SELECT 1 FROM ontime.flight LIMIT 1"
    db.error = QueryCanceled("canceling statement due to statement timeout")
    with pytest.raises(QueryTimeout):
        executor.fetch(sql)
    db.error = RuntimeError("boom password=s3cret-pw")
    with pytest.raises(QueryExecutionFailed) as e:
        executor.fetch(sql)
    assert "s3cret" not in str(e.value)
    db.error = None
    db.connect_error = RuntimeError("postgresql://u:s3cret-pw@h/db refused")
    with pytest.raises(DatabaseUnavailable) as e:
        executor.fetch(sql)
    assert "s3cret" not in str(e.value)


def test_reference_date():
    assert resolve_reference_date("2026-01-31") == date(2026, 1, 31)
    # No override: today's date, never derived from the data in the database.
    assert resolve_reference_date(None) == date.today()
    assert resolve_reference_date("  ") == date.today()


def test_explain_guard():
    assert check_cost(lambda s, p: 10.0, "x", None, 100) is True
    with pytest.raises(SqlGuardRejection):
        check_cost(lambda s, p: 1e9, "x", None, 100)


def col(name, st):
    return ResultColumn(name=name, role="metric" if st == "metric" else "dimension", label=name, semantic_type=st, data_type="string")


class M:
    def __init__(self, name="flight_count", unit="count"):
        self.name, self.unit = name, unit


T, C, MET = "time_dimension", "dimension", "metric"


@pytest.mark.parametrize("cols,n,metric,expect", [
    ([], 1, M(), ("kpi", None, None)),
    ([col("d", T)], 5, M(), ("line", "d", None)),
    ([col("d", T), col("c", C)], 5, M("r", "ratio"), ("line", "d", "c")),
    ([col("d", T), col("c", C)], 5, M(), ("stacked_bar", "d", "c")),
    ([col("c", C)], 5, M(), ("bar", "c", None)),
    ([col("c", C)], 25, M(), ("bar", "c", None)),
    ([col("a", C), col("b", C)], 5, M(), ("table", None, None)),
    ([], 3, M(), ("table", None, None)),
])
def test_chart_hints(cols, n, metric, expect):
    hints = rf.chart_hints(cols + [col(metric.name, MET)], [metric], n)
    assert hints[-1].chart_type == "table" and hints[-1].confidence == 1.0
    if expect[0] == "table":
        assert len(hints) == 1
        return
    h = hints[0]
    assert (h.chart_type, h.x_field, h.color_field) == expect and h.confidence == 0.9
    assert h.y_fields == [metric.name]
    assert (h.title == "Top 20") == (n > 20 and expect[0] == "bar")


def test_value_conversion():
    assert rf._value(Decimal("1.5")) == 1.5 and rf._value(date(2026, 1, 2)) == "2026-01-02"
