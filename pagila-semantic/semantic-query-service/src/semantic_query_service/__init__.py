"""HTTP API in front of the read-only Pagila semantic layer."""
from .app import create_app

__all__ = ["create_app"]
