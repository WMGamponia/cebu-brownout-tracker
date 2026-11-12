"""Nothing private in the committed tree (Phase 6 spec 1, 4.8): the repo is public."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
ALLOWED_DOMAINS = ("users.noreply.github.com", "github.com", "anthropic.com")
ALLOWED_TLDS = (".test", ".invalid", ".example")


def _tracked() -> list[Path]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout
    return [ROOT / p.decode() for p in out.split(b"\0") if p]


def test_no_private_email_in_tracked_files():
    hits = []
    for path in _tracked():
        if not path.is_file() or path.suffix in {".png", ".gz", ".zip"}:
            continue
        for m in EMAIL.finditer(path.read_bytes()):
            domain = m.group(1).decode().lower()
            if domain in ALLOWED_DOMAINS or domain.endswith(ALLOWED_TLDS):
                continue
            hits.append(f"{path.relative_to(ROOT).as_posix()}: @{domain}")
    assert not hits, "email addresses in tracked files (domains only):\n" + "\n".join(hits)
