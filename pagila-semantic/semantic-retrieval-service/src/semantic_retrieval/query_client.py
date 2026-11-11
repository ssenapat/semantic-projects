from __future__ import annotations

import time

import httpx


class CatalogUnavailable(Exception):
    pass


class CatalogSource:
    """Fetches the catalog from the query service and caches it briefly."""

    def __init__(self, base_url: str, api_key: str | None = None, ttl: float = 300.0,
                 client: httpx.Client | None = None):
        headers = {"X-API-Key": api_key} if api_key else {}
        self._http = client or httpx.Client(base_url=base_url, headers=headers, timeout=10.0)
        self._ttl, self._cached, self._policy = ttl, None, None

    def get(self) -> dict:
        now = time.monotonic()
        if self._cached and now - self._cached[0] < self._ttl:
            return self._cached[1]
        try:
            r = self._http.get("/v1/catalog")
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise CatalogUnavailable(type(exc).__name__) from exc
        self._cached = (now, r.json())
        return self._cached[1]

    def policy(self) -> dict:
        """Query policy limits from GET /v1/context, cached like the catalog."""
        now = time.monotonic()
        if self._policy and now - self._policy[0] < self._ttl:
            return self._policy[1]
        try:
            r = self._http.get("/v1/context")
            r.raise_for_status()
            policy = r.json()["policy"]
            policy["max_measures_per_query"], policy["max_dimensions_per_query"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise CatalogUnavailable(type(exc).__name__) from exc
        self._policy = (now, policy)
        return policy
