"""The admin token: written fresh by each `serve` start, read by the tray (tray spec 5.1).

It lives in `data/` (gitignored) and, on Windows, is readable by the owner's account only. It
keeps out web pages and other Windows users, not programs running as the owner: those can
already write the database.
"""

from __future__ import annotations

import logging
import os
import secrets
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("outages")

TOKEN_FILE = "admin.token"


def _owner_only(path: Path) -> None:
    if sys.platform != "win32":
        os.chmod(path, 0o600)
        return
    user = os.environ.get("USERNAME")
    if not user:
        log.warning("admin token: USERNAME not set; %s keeps its inherited access", path)
        return
    try:
        r = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("admin token: could not restrict %s: %s", path, e)
        return
    if r.returncode != 0:
        log.warning("admin token: icacls failed on %s: %s", path, (r.stderr or r.stdout).strip())


def write_new(data_dir: Path) -> str:
    """A new random token in `data_dir/admin.token`; returns it."""
    data_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path = data_dir / TOKEN_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(token, encoding="ascii")
    os.replace(tmp, path)
    _owner_only(path)
    return token


def read(data_dir: Path) -> str | None:
    """The current token, or None when there is none (or it can't be read)."""
    try:
        return (data_dir / TOKEN_FILE).read_text(encoding="ascii").strip() or None
    except OSError:
        return None
