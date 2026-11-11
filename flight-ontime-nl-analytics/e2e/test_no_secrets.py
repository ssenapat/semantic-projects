"""REQ-010/011/080/081: no credentials in files; every bind address is loopback."""
from __future__ import annotations

import re
from pathlib import Path

TS = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".venv", ".run", "__pycache__", ".pytest_cache", "node_modules", ".git"}
TEXT_SUFFIXES = {".py", ".sh", ".md", ".yml", ".yaml", ".toml", ".json", ".txt", ".ini", ".cfg", ".env"}
SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)\b(pgpassword|password|api_key|apikey|secret)\b\s*[:=]\s*['\"][^'\"\s${}]{4,}['\"]"),
    re.compile(r"postgres(?:ql)?://[^:/\s]+:[^@\s${}]+@"),
]


# Dummy value that a Query unit test feeds to the error redactor to prove it is hidden; not a credential.
KNOWN_DUMMIES = ("s3cret-pw",)


def _files():
    for p in TS.rglob("*"):
        if p.is_file() and p.suffix in TEXT_SUFFIXES and not (set(p.relative_to(TS).parts) & SKIP_DIRS):
            yield p


def test_no_credentials_in_files():
    me = Path(__file__).resolve()
    hits = []
    for p in _files():
        if p == me:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for dummy in KNOWN_DUMMIES:
            text = text.replace(dummy, "${DUMMY}")
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                hits.append(f"{p.relative_to(TS)} matches {pat.pattern[:30]}")
    assert not hits, hits


def test_runner_binds_loopback_only():
    text = (TS / "run-local.sh").read_text()
    assert "0.0.0.0" not in text
    assert "--server.address 127.0.0.1" in text
    for var in ("QUERY_HOST", "PARSER_HOST", "RETRIEVER_HOST"):
        assert f'{var}="${{{var}:-127.0.0.1}}"' in text


def test_service_host_defaults_are_loopback():
    for folder in ("semantic-retrieval-service", "llm-intent-parser-service", "semantic-query-service"):
        for cfg in (TS / folder).rglob("config.py"):
            if ".venv" in cfg.parts:
                continue
            assert "0.0.0.0" not in cfg.read_text(), cfg


def test_runner_never_echoes_secret_values():
    text = (TS / "run-local.sh").read_text()
    assert not re.search(r"echo[^\n]*\$\{?(PGPASSWORD|ANTHROPIC_API_KEY)\b[^:}]", text)
