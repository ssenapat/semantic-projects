"""HTTP client for the LLM Intent Parser (CONTRACT 4.2, 5.1)."""
from __future__ import annotations

import httpx
from pydantic import ValidationError

from ..schemas.errors import ApiError
from ..schemas.parser import IntentParserRequest, intent_parser_response_adapter
from ._http import post_json


class ParserClient:
    def __init__(self, base_url: str, timeout_seconds: float = 25, client: httpx.Client | None = None):
        self._url = base_url.rstrip("/") + "/api/v1/intent/parse"
        self._timeout = timeout_seconds
        self._client = client or httpx.Client()

    def parse(self, request: IntentParserRequest):
        data = post_json(self._client, self._url, request.model_dump(mode="json"), request.request_id,
                         "intent_parser_unavailable", "intent parser", self._timeout)
        try:
            return intent_parser_response_adapter.validate_python(data)
        except ValidationError:
            raise ApiError("intent_parser_unavailable", "The intent parser returned an invalid response.",
                           request.request_id)
