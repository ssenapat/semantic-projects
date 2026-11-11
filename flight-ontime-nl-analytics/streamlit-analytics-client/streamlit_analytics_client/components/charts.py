"""Render charts/tables from chart hints (CONTRACT §6.6)."""
from __future__ import annotations

from typing import List, Optional

import altair as alt
import pandas as pd

TOP_N = 20


def to_dataframe(result: dict) -> pd.DataFrame:
    cols = [c["name"] for c in result.get("columns", [])]
    return pd.DataFrame(result.get("rows", []), columns=cols or None)


def select_view(result: dict) -> str:
    """C-2.9.1: choose the default view from the response shape."""
    cols = result.get("columns", [])
    dims = [c for c in cols if c.get("role") == "dimension"]
    mets = [c for c in cols if c.get("role") == "metric"]
    units = {c.get("unit") for c in mets}
    if "count" in units and units & {"ratio", "percent"}:
        return "table"
    if len(dims) > 2:
        return "table"
    if len(dims) == 2:
        return "pivot"
    if len(dims) == 1 and len(mets) == 1:
        return "bar"
    if len(dims) == 1 and len(mets) > 1:
        return "grouped_bar"
    if len(dims) == 0 and len(mets) == 1:
        return "kpi"
    return "table"


def pivot_frame(result: dict, df: pd.DataFrame) -> pd.DataFrame:
    """Pivot table: index = first dimension, columns = second dimension, values = metric columns."""
    cols = result.get("columns", [])
    dims = [c["name"] for c in cols if c.get("role") == "dimension"]
    mets = [c["name"] for c in cols if c.get("role") == "metric"]
    return pd.pivot_table(df, index=dims[0], columns=dims[1], values=mets, aggfunc="sum")


def build_grouped_bar(result: dict, df: pd.DataFrame, title: str = "") -> Optional[alt.Chart]:
    """Altair grouped bars: fold the metric columns (x = dimension, xOffset/color = metric)."""
    cols = result.get("columns", [])
    dims = [c["name"] for c in cols if c.get("role") == "dimension"]
    mets = [c["name"] for c in cols if c.get("role") == "metric"]
    if len(dims) != 1 or len(mets) < 2 or df.empty:
        return None
    d = df.head(TOP_N)
    label_of = {c["name"]: c.get("label") or c["name"] for c in cols}
    long = d.melt(id_vars=[dims[0]], value_vars=mets, var_name="metric", value_name="value")
    long["metric"] = long["metric"].map(label_of)
    return alt.Chart(long, title=title or "Chart").mark_bar().encode(
        x=alt.X(dims[0]), y=alt.Y("value:Q"), xOffset="metric:N", color="metric:N")


def build_chart(hint: dict, df: pd.DataFrame, fallback_title: str = "") -> Optional[alt.Chart]:
    """Return an Altair chart for the hint, or None for table/kpi/unplottable hints."""
    ct = hint.get("chart_type")
    x, ys, color = hint.get("x_field"), hint.get("y_fields") or [], hint.get("color_field")
    if ct in ("table", "kpi") or not x or not ys or df.empty:
        return None
    if x not in df.columns or any(y not in df.columns for y in ys):
        return None
    y = ys[0]
    d = df.head(TOP_N) if ct == "bar" else df
    enc = {"x": alt.X(x), "y": alt.Y(y)}
    if color and color in d.columns:
        enc["color"] = alt.Color(color)
    base = alt.Chart(d, title=hint.get("title") or fallback_title or "Chart")
    if ct == "line":
        return base.mark_line(point=True).encode(**enc)
    if ct in ("bar", "stacked_bar"):
        return base.mark_bar().encode(**enc)
    if ct == "area":
        return base.mark_area().encode(**enc)
    if ct == "scatter":
        return base.mark_circle().encode(**enc)
    return None


def hint_types(hints: List[dict]) -> List[str]:
    seen: List[str] = []
    for h in hints:
        if h.get("chart_type") not in seen:
            seen.append(h["chart_type"])
    return seen
