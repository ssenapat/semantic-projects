class SemanticError(Exception):
    """Base class for all errors raised by the semantic layer."""


class DefinitionError(SemanticError):
    """A definition file is invalid or inconsistent."""


class RequestError(SemanticError):
    """A semantic request is invalid, unsupported or not allowed."""


class UnsupportedRequest(RequestError):
    """A well-formed request the semantic layer cannot answer (mixed models, tag or dimension not allowed)."""

    def __init__(self, message: str, code: str, details: dict):
        super().__init__(message)
        self.code = code
        self.details = details


class GuardError(SemanticError):
    """Generated SQL was rejected by the SQL guard."""


class QueryTimeoutError(SemanticError):
    """The database cancelled the query because it exceeded the statement timeout."""
