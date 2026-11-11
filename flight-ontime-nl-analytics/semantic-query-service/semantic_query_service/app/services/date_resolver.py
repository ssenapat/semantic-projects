"""Relative/absolute period resolution (CONTRACT §6.4, REQ-048/049). End is exclusive."""
from __future__ import annotations

from datetime import date, timedelta

from ..schemas.query_plan import ResolvedDateRange
from ..schemas.semantic_request import SemanticDateRange

MAX_SPAN_DAYS = 3660


class InvalidDateRange(ValueError):
    """The requested range cannot be resolved (maps to unsupported ``unsupported_operation``)."""


def _first_of_month(d: date, offset_months: int = 0) -> date:
    idx = d.year * 12 + (d.month - 1) + offset_months
    return date(idx // 12, idx % 12 + 1, 1)


def relative_bounds(period: str, r: date) -> tuple[date, date]:
    one = timedelta(days=1)
    if period == "today":
        return r, r + one
    if period == "yesterday":
        return r - one, r
    if period == "last_7_days":
        return r - timedelta(days=6), r + one
    if period == "last_30_days":
        return r - timedelta(days=29), r + one
    if period == "last_complete_calendar_month":
        return _first_of_month(r, -1), _first_of_month(r)
    if period == "last_3_complete_months":
        return _first_of_month(r, -3), _first_of_month(r)
    if period == "last_6_complete_months":
        return _first_of_month(r, -6), _first_of_month(r)
    if period == "quarter_to_date":
        return date(r.year, 3 * ((r.month - 1) // 3) + 1, 1), r + one
    if period == "year_to_date":
        return date(r.year, 1, 1), r + one
    raise InvalidDateRange(f"unknown relative period {period!r}")


def _iso(value: str | None, label: str) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise InvalidDateRange(f"{label} must be an ISO date (YYYY-MM-DD)")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise InvalidDateRange(f"{label} must be a valid ISO date (YYYY-MM-DD)") from None


def resolve(dr: SemanticDateRange, reference_date: date, time_basis: str) -> ResolvedDateRange:
    if dr.kind == "relative":
        if dr.relative_period is None:
            raise InvalidDateRange("relative date range needs a relative_period")
        start, end = relative_bounds(dr.relative_period, reference_date)
    else:
        start = _iso(dr.start_date, "start_date")
        end_incl = _iso(dr.end_date, "end_date")
        if start > end_incl:
            raise InvalidDateRange("start_date must not be after end_date")
        end = end_incl + timedelta(days=1)
        if (end - start).days > MAX_SPAN_DAYS:
            raise InvalidDateRange(f"date range spans more than {MAX_SPAN_DAYS} days")
    return ResolvedDateRange(start_inclusive=start.isoformat(), end_exclusive=end.isoformat(),
                             time_basis=time_basis)
