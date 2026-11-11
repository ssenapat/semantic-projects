"""Value maps loaded through the guarded read-only executor (CONTRACT §6.5, REQ-050/051, HC-4)."""
from __future__ import annotations

import os
from typing import Callable, Iterable

AIRPORT_SQL = "SELECT DISTINCT code, city_name, state_abr FROM ontime.airport"
CARRIER_SQL = "SELECT DISTINCT unique_carrier FROM ontime.carrier"


def _always_clarify_from_env() -> list[str]:
    raw = os.environ.get("QUERY_ALWAYS_CLARIFY_LOCATIONS", "Chicago")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _city_keys(city: str) -> set[str]:
    full = city.strip().lower()
    keys = {full}
    if "," in full:  # e.g. "Chicago, IL"
        keys.add(full.split(",", 1)[0].strip())
    return keys


class MappingRepository:
    def __init__(self, always_clarify: Iterable[str] | None = None):
        src = always_clarify if always_clarify is not None else _always_clarify_from_env()
        self.always_clarify = {s.strip().lower() for s in src if s and s.strip()}
        self._airports: dict[str, str] = {}          # upper code -> code
        self._cities: dict[str, set[str]] = {}       # lower city key -> codes
        self._carriers: dict[str, str] = {}          # upper code -> code
        self._loaded = False

    def load(self, fetch: Callable[[str, dict | None], list[dict]]) -> None:
        airports = fetch(AIRPORT_SQL, None)
        carriers = fetch(CARRIER_SQL, None)
        a: dict[str, str] = {}
        c: dict[str, set[str]] = {}
        for row in airports:
            code = row.get("code")
            if not code:
                continue
            code = str(code).strip()
            a[code.upper()] = code
            if row.get("city_name"):
                for k in _city_keys(str(row["city_name"])):
                    c.setdefault(k, set()).add(code)
        self._airports, self._cities = a, c
        self._carriers = {str(r["unique_carrier"]).strip().upper(): str(r["unique_carrier"]).strip()
                          for r in carriers if r.get("unique_carrier")}
        self._loaded = True

    @property
    def loaded(self) -> bool:
        return self._loaded

    def resolve_airport(self, value: str) -> tuple[str, object]:
        """('code', code) | ('ambiguous', sorted codes) | ('unresolved', None)."""
        v = value.strip()
        if v.upper() in self._airports:
            return "code", self._airports[v.upper()]
        key = v.lower()
        codes = self._cities.get(key, set())
        if key in self.always_clarify or len(codes) > 1:
            return "ambiguous", sorted(codes)
        if len(codes) == 1:
            return "code", next(iter(codes))
        return "unresolved", None

    def resolve_carrier(self, value: str) -> str | None:
        """Exact unique_carrier code only (display names are unmapped)."""
        return self._carriers.get(value.strip().upper())
