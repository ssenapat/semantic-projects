"""Entity value resolution: airports and carriers (CONTRACT §6.5, REQ-050/051)."""
from __future__ import annotations

from ..repositories.mapping_repository import MappingRepository
from .ambiguity_handler import NeedsClarification, Unresolvable

AIRPORT_ROLES = {"origin_airport", "destination_airport"}


def is_airport_code_dimension(dim) -> bool:
    return getattr(dim, "role", None) in AIRPORT_ROLES and dim.column == "code"


def is_carrier_dimension(dim) -> bool:
    return dim.name == "carrier_code"


def resolve_values(dim, values: list[str], mappings: MappingRepository) -> list[str] | None:
    """Resolved values, or None when the dimension is not entity-coded.
    Raises NeedsClarification / Unresolvable."""
    if is_airport_code_dimension(dim):
        out: list[str] = []
        for v in values:
            kind, res = mappings.resolve_airport(v)
            if kind == "code":
                out.append(res)  # type: ignore[arg-type]
            elif kind == "ambiguous":
                opts = list(res)  # type: ignore[arg-type]
                raise NeedsClarification(
                    f"'{v}' could refer to more than one airport. Which airport do you mean?",
                    "airport_or_location", opts)
            else:
                raise Unresolvable(f"Could not resolve '{v}' to a known airport.")
        return out
    if is_carrier_dimension(dim):
        out = []
        for v in values:
            code = mappings.resolve_carrier(v)
            if code is None:
                raise Unresolvable(f"Carrier '{v}' is not a known carrier code; carrier display "
                                   "names are not approved for use.")
            out.append(code)
        return out
    return None
