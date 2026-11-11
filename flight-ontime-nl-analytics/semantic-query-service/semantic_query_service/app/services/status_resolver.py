"""Enumerated-value resolution (e.g. cancellation reason labels, weekday names) (REQ-050/051)."""
from __future__ import annotations

from .ambiguity_handler import Unresolvable


def resolve_values(dim, values: list[str]) -> list[str] | None:
    """Map labels or keys of a dimension's enumerated ``values`` to keys (as strings).
    None when the dimension has no enumerated dict. Raises Unresolvable."""
    enum = dim.values
    if not isinstance(enum, dict) or not enum:
        return None
    by_key = {str(k).lower(): str(k) for k in enum}
    by_label = {str(lbl).lower(): str(k) for k, lbl in enum.items()}
    out: list[str] = []
    for v in values:
        key = v.strip().lower()
        hit = by_key.get(key) or by_label.get(key)
        if hit is None:
            raise Unresolvable(f"'{v}' is not a recognised value for {dim.label.lower()}.")
        out.append(hit)
    return out
