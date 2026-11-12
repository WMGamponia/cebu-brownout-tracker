"""A separate process holding the serve lock (tests of Phase 6 spec 4.4)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")

HOLDER = """
import os, sys
from pathlib import Path
from outages.lock import acquire
lk = acquire(Path(sys.argv[1]))
print("ready", os.getpid(), flush=True)
sys.stdin.read()
lk.release()
"""


class Holder:
    """A separate process that holds the lock until closed."""

    def __init__(self, path: Path):
        env = dict(os.environ, PYTHONPATH=SRC)
        self.proc = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
            text=True,
        )
        word, pid = self.proc.stdout.readline().split()
        assert word == "ready"
        self.pid = int(pid)  # the venv python.exe may be a launcher: not proc.pid

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.wait(timeout=20)
