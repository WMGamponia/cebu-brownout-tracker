"""Event keys already notified, kept in `data/tray_state.json` so a tray restart doesn't repeat
them (tray spec 5.4). A missing, unreadable or malformed file counts as empty."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from outages.tray.state import Event

log = logging.getLogger("outages")

KEEP = 500


class Seen:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._keys: list[str] = []

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        keys = data.get("seen") if isinstance(data, dict) else None
        self._keys = [k for k in keys if isinstance(k, str)] if isinstance(keys, list) else []

    def fresh(self, events: list[Event]) -> list[Event]:
        """The events not notified before; marks and saves them."""
        known = set(self._keys)
        new = []
        for e in events:
            if e.key not in known:
                known.add(e.key)
                new.append(e)
                self._keys.append(e.key)
        if new:
            self._keys = self._keys[-KEEP:]
            self._save()
        return new

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"seen": self._keys}), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("tray: could not save %s: %s", self.path, e)
