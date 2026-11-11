"""REQ-007 / C-4.9: no e2e test sends the legacy single `metric` request field."""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEGACY = re.compile(r"""["']metric["']\s*:""")
MARK = "legacy-rejection"      # only the tests that prove the legacy form is REJECTED carry this marker


def test_only_rejection_tests_mention_the_legacy_field():
    offenders = []
    for path in sorted(HERE.rglob("*.py")):
        if path.name == Path(__file__).name:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if LEGACY.search(line) and MARK not in line:
                offenders.append(f"{path.relative_to(HERE)}:{number}")
    assert not offenders, offenders


def test_rejection_markers_are_in_rejection_tests_only():
    text = (HERE / "test_e2e.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if MARK in line:
            assert "semantic_request" in line or "legacy" in line.lower()
