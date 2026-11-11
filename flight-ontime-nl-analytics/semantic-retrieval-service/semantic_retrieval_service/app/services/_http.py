"""Shared downstream HTTP call with CONTRACT 5.1 error mapping."""
from __future__ import annotations

import httpx

from ..schemas.errors import ApiError


def post_json(client: httpx.Client, url: str, body: dict, request_id: str, unavailable_code: str,
              what: str, timeout: float):
    try:
        resp = client.post(url, json=body, headers={"X-Request-ID": request_id}, timeout=timeout)
    except httpx.TimeoutException:
        raise ApiError(unavailable_code, f"The {what} timed out.", request_id)
    except httpx.HTTPError:
        raise ApiError(unavailable_code, f"The {what} could not be reached.", request_id)
    if resp.status_code == 409:
        raise ApiError("catalog_version_mismatch", f"The {what} reported a catalog version mismatch.", request_id)
    if resp.status_code == 429:
        raise ApiError("rate_limited", f"The {what} is rate limiting requests.", request_id)
    if resp.status_code != 200:
        raise ApiError(unavailable_code, f"The {what} returned an error (HTTP {resp.status_code}).", request_id)
    try:
        return resp.json()
    except ValueError:
        raise ApiError(unavailable_code, f"The {what} returned an unreadable response.", request_id)
