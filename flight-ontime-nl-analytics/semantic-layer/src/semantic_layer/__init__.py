"""Read-only semantic layer over the ``tran-stats`` database (schema ``ontime``)."""
from .loader import load_catalog

__version__ = "0.1.0"
__all__ = ["__version__", "load_catalog"]
