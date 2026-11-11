"""Pure helpers behind the screen: no Streamlit here, so they are easy to test."""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

MAX_CHARTS = 3
MAX_BARS = 30
_MD_SPECIALS = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$:&])")
_CSV_RISKY = ("=", "+", "-", "@", "\t", "\r")


def plain(text: Any) -> str:
    """Escape Markdown so text from the model or the user can never render as links, images or directives."""
    return _MD_SPECIALS.sub(r"\\\1", str(text))


def time_dimensions(request: dict) -> list[str]:
    """Names of the time dimensions in a request ('rental_date:month' -> 'rental_date')."""
    return [d.split(":")[0] for d in request.get("dimensions") or [] if ":" in d]


def dimension_names(request: dict) -> list[str]:
    return [d.split(":")[0] for d in request.get("dimensions") or []]


def answer_frame(answer: dict, request: dict) -> pd.DataFrame:
    columns = [c["name"] if isinstance(c, dict) else c for c in answer.get("columns") or []]
    df = pd.DataFrame(answer.get("rows") or [], columns=columns or None)
    for name in time_dimensions(request):
        if name in df:
            df[name] = pd.to_datetime(df[name], errors="coerce")
    return df


def describe_request(request: dict) -> str:
    """One readable line: 'total_revenue by rental_date:month, payment_store'."""
    text = ", ".join(request.get("measures") or [])
    if request.get("dimensions"):
        text += " by " + ", ".join(request["dimensions"])
    if request.get("filters"):
        text += f" ({len(request['filters'])} filter{'s' if len(request['filters']) > 1 else ''})"
    return text


def chart_plans(request: dict, df: pd.DataFrame) -> list[dict]:
    """Decide what to draw. Each measure gets its own chart because they may use different units."""
    dims = [d for d in dimension_names(request) if d in df]
    measures = [m for m in request.get("measures") or [] if m in df][:MAX_CHARTS]
    times = [d for d in time_dimensions(request) if d in df]
    if not dims or df.empty or not measures:
        return []
    if len(times) == 1 and len(dims) == 1 and len(df) > 1:
        return [{"kind": "line", "x": times[0], "y": m} for m in measures]
    if len(times) == 1 and len(dims) == 2 and len(df) > 1:
        other = next(d for d in dims if d != times[0])
        if df[other].nunique() <= 8:
            return [{"kind": "line", "x": times[0], "y": m, "color": other} for m in measures]
        return []
    if not times and len(dims) == 1 and 1 < len(df) <= MAX_BARS:
        return [{"kind": "bar", "x": dims[0], "y": m} for m in measures]
    return []


def column_meta(answer: dict) -> dict[str, dict]:
    """{name: {role, label, unit, type}} from the answer's ColumnMeta list (§3.3); bare names get defaults."""
    meta: dict[str, dict] = {}
    for c in answer.get("columns") or []:
        if isinstance(c, dict):
            name = c["name"]
            meta[name] = {
                "role": c.get("role", "dimension"),
                "label": c.get("label") or name,
                "unit": c.get("unit"),
                "type": c.get("type", "string"),
            }
        else:
            meta[str(c)] = {"role": "dimension", "label": str(c), "unit": None, "type": "string"}
    return meta


def format_value(value: Any, unit: str | None) -> str:
    """Show a cell the way people read it: grouped thousands, fixed decimals per unit."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    if unit == "currency" and isinstance(value, (int, float)):
        return f"{value:,.2f}"
    if unit == "count" and isinstance(value, (int, float)):
        return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
    if unit == "days" and isinstance(value, (int, float)):
        return f"{value:,.2f}"
    return str(value)


def display_frame(df: pd.DataFrame, meta: dict[str, dict]) -> pd.DataFrame:
    """One column per dimension and per measure, labelled and formatted for reading."""
    out = pd.DataFrame(index=df.index)
    used: set[str] = set()
    for name in df.columns:
        m = meta.get(name, {})
        unit = m.get("unit")
        label = m.get("label") or name
        if label in used:
            label = f"{label} ({name})"
        used.add(label)
        out[label] = [format_value(v, unit) for v in df[name]]
    return out


def chart_frame(df: pd.DataFrame, plan: dict) -> pd.DataFrame:
    """Frame to draw: line plans are sorted ascending by x."""
    if plan.get("kind") == "line" and plan.get("x") in df:
        return df.sort_values(plan["x"], kind="stable").reset_index(drop=True)
    return df


def csv_bytes(df: pd.DataFrame) -> bytes:
    """CSV that is safe to open in a spreadsheet: text cells that look like formulas are defanged."""
    safe = df.copy()
    for col in safe.columns:
        if pd.api.types.is_object_dtype(safe[col]) or pd.api.types.is_string_dtype(safe[col]):
            safe[col] = safe[col].map(lambda v: "'" + v if isinstance(v, str) and v.startswith(_CSV_RISKY) else v)
    return safe.to_csv(index=False).encode("utf-8")


def next_previous_request(turns: list[dict]) -> dict | None:
    """The request a follow-up question builds on: the latest turn that was answered."""
    for turn in reversed(turns):
        result = turn.get("result") or {}
        if result.get("status") == "ready" and result.get("answer"):
            return result["request"]
    return None
