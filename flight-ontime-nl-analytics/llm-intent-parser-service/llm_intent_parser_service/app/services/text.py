import re


def normalize(text: str) -> str:
    """Lowercase, replace non-alphanumerics by a space and collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()
