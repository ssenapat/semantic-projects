"""Pure value/table formatting (CONTRACT C-2.9.3 to C-2.9.5). Must not import streamlit."""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd


def format_value(value: Any, unit: Optional[str]) -> str:
    if value is None:
        return ""
    if unit == "count":
        return f"{int(round(value)):,}"
    if unit == "ratio":
        return f"{value * 100:.2f}%"
    if unit == "percent":
        return f"{value:.2f}%"
    return str(value)


def formatted_table(result: dict) -> pd.DataFrame:
    cols = result.get("columns", [])
    out = {}
    for i, c in enumerate(cols):
        vals = [row[i] if i < len(row) else None for row in result.get("rows", [])]
        if c.get("role") == "metric":
            cells = [format_value(v, c.get("unit")) for v in vals]
        else:
            cells = ["" if v is None else str(v) for v in vals]
        out[i] = cells
    df = pd.DataFrame(out, columns=list(range(len(cols))))
    df.columns = [c.get("label") or c.get("name") for c in cols]
    return df


def truncation_notice(result: dict) -> Optional[str]:
    if result.get("truncated") is True:
        return f"Results were truncated: showing the first {result.get('row_count')} rows."
    return None
