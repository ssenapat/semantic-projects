"""HTTP client for the Semantic Query Service (CONTRACT 4.3, 5.1)."""
from __future__ import annotations

import httpx
from pydantic import ValidationError

from ..schemas.errors import ApiError
from ..schemas.result import QueryExecuteRequest, query_response_adapter
from ._http import post_json


class QueryServiceClient:
    def __init__(self, base_url: str, timeout_seconds: float = 40, client: httpx.Client | None = None):
        self._url = base_url.rstrip("/") + "/api/v1/query/execute"
        self._timeout = timeout_seconds
        self._client = client or httpx.Client()

    def execute(self, request: QueryExecuteRequest):
        data = post_json(self._client, self._url, request.model_dump(mode="json"), request.request_id,
                         "query_service_unavailable", "query service", self._timeout)
        try:
            return query_response_adapter.validate_python(data)
        except ValidationError:
            raise ApiError("query_service_unavailable", "The query service returned an invalid response.",
                           request.request_id)
