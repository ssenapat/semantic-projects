from datetime import date

import pytest

from semantic_query_service.app.schemas.semantic_request import SemanticDateRange
from semantic_query_service.app.services.date_resolver import InvalidDateRange, resolve


def rel(p, r):
    x = resolve(SemanticDateRange(kind="relative", relative_period=p), r, "fl_date")
    return x.start_inclusive, x.end_exclusive


def test_golden_last_complete_month():
    assert rel("last_complete_calendar_month", date(2026, 1, 31)) == ("2025-12-01", "2026-01-01")


R = date(2026, 5, 17)


@pytest.mark.parametrize("p,exp", [
    ("today", ("2026-05-17", "2026-05-18")),
    ("yesterday", ("2026-05-16", "2026-05-17")),
    ("last_7_days", ("2026-05-11", "2026-05-18")),
    ("last_30_days", ("2026-04-18", "2026-05-18")),
    ("last_complete_calendar_month", ("2026-04-01", "2026-05-01")),
    ("last_3_complete_months", ("2026-02-01", "2026-05-01")),
    ("last_6_complete_months", ("2025-11-01", "2026-05-01")),
    ("quarter_to_date", ("2026-04-01", "2026-05-18")),
    ("year_to_date", ("2026-01-01", "2026-05-18")),
])
def test_relative_table(p, exp):
    assert rel(p, R) == exp


def test_year_boundary_and_time_basis():
    assert rel("last_3_complete_months", date(2026, 2, 10)) == ("2025-11-01", "2026-02-01")
    out = resolve(SemanticDateRange(kind="relative", relative_period="today"), R, "fl_date")
    assert out.time_basis == "fl_date"


def test_absolute():
    d = SemanticDateRange(kind="absolute", start_date="2026-01-01", end_date="2026-01-31")
    out = resolve(d, R, "fl_date")
    assert (out.start_inclusive, out.end_exclusive) == ("2026-01-01", "2026-02-01")


@pytest.mark.parametrize("s,e", [("2026-02-01", "2026-01-01"), ("2026-13-01", "2026-14-01"),
                                 ("x", "y"), ("2000-01-01", "2026-01-01")])
def test_absolute_invalid(s, e):
    with pytest.raises(InvalidDateRange):
        resolve(SemanticDateRange(kind="absolute", start_date=s, end_date=e), R, "fl_date")
