"""Exception hierarchy for the semantic layer (CONTRACT C-6.1)."""
from __future__ import annotations


class SemanticLayerError(Exception):
    """Base class for all semantic-layer errors."""


class DefinitionError(SemanticLayerError):
    """A definition file is invalid. str() = ``{file}:{location}: {message}``."""

    def __init__(self, file: str, location: str, message: str) -> None:
        self.file = file
        self.location = location
        self.message = message
        super().__init__(file, location, message)

    def __str__(self) -> str:
        return f"{self.file}:{self.location}: {self.message}"


class PolicyViolation(SemanticLayerError):
    """SQL or an object violates a policy rule (CONTRACT C-5)."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = message if message is not None else code
        super().__init__(code, self.message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" if self.message != self.code else self.code


class ConfigError(SemanticLayerError):
    """Configuration problem. Names missing env vars only, never values."""


class DatabaseError(SemanticLayerError):
    """Database failure: psycopg error class + message with password/DSN removed."""
