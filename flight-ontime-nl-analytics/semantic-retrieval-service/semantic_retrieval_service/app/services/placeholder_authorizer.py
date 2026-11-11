"""Permissive placeholder authorization (REQ-024): all eligible domains, no enforcement."""
from __future__ import annotations

from ..schemas.ask import AuthorizationContext

AUTHORIZATION_MODE = "permissive_placeholder"


class PlaceholderAuthorizer:
    mode = AUTHORIZATION_MODE

    def allowed_domains(self, ctx: AuthorizationContext) -> list[str] | None:
        return None  # None = every eligible domain
