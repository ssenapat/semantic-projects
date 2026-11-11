"""Parses raw model text into the discriminated outcome union. Never repairs silently."""
import json

from pydantic import TypeAdapter, ValidationError

from ..schemas.response import IntentParserResponse

_ADAPTER = TypeAdapter(IntentParserResponse)


def outcome_json_schema() -> dict:
    return _ADAPTER.json_schema()


def _extract_json(raw: str) -> str:
    text = raw.strip()
    if text.startswith("{"):
        return text
    start, end = text.find("{"), text.rfind("}")  # tolerate wrapping prose / fences from the provider
    return text[start:end + 1] if 0 <= start < end else text


def parse_outcome(raw: str):
    """Returns (outcome | None, errors)."""
    try:
        data = json.loads(_extract_json(raw))
    except (ValueError, TypeError):
        return None, ["output is not valid JSON"]
    if not isinstance(data, dict):
        return None, ["output must be one JSON object"]
    try:
        return _ADAPTER.validate_python(data), []
    except ValidationError as exc:
        errs = [f"{'.'.join(str(p) for p in e['loc'])}: {e['type']}" for e in exc.errors()][:10]
        return None, errs or ["output does not match the schema"]
