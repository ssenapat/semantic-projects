"""Request shapes. Types and sizes are checked here; meaning is checked by the compiler."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

Name = Field(min_length=1, max_length=64)


class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: str = Name
    op: str = Field(min_length=1, max_length=16)
    value: Any = None


class OrderBy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Name
    direction: Literal["asc", "desc"] = "asc"


class SemanticRequest(BaseModel):
    """Spec section 10. Never contains SQL. A legacy singular `measure` field is refused (extra=forbid).

    The list-size limits come from the query policy: build the served model with `request_model(policy)`."""
    model_config = ConfigDict(extra="forbid")
    model: str = Name
    measures: list[str] = Field(min_length=1)                         # max = policy.max_measures_per_query
    dimensions: list[str] | None = Field(default=None)                # max = policy.max_dimensions_per_query
    filters: list[Filter] | None = Field(default=None, max_length=20)
    order_by: list[OrderBy] | None = Field(default=None, max_length=20)
    limit: int | None = Field(default=None, ge=1, le=1_000_000)
    timezone: str | None = Field(default=None, max_length=64)
    accept_fan_out: bool | None = None

    def to_layer(self) -> dict:
        return self.model_dump(exclude_none=True)


def request_model(policy: dict) -> type[SemanticRequest]:
    """SemanticRequest with maxItems taken from the policy (single source), never from literals."""
    return create_model(
        "SemanticRequest", __base__=SemanticRequest,
        measures=(list[str], Field(min_length=1, max_length=int(policy["max_measures_per_query"]))),
        dimensions=(list[str] | None, Field(default=None, max_length=int(policy["max_dimensions_per_query"]))),
    )
