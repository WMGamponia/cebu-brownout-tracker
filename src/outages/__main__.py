"""Command line: `python -m outages backfill|check|reparse|report|serve` (spec 7)."""

from __future__ import annotations

import argparse
import sys

COMMANDS = {
    "backfill": "fetch and store every interruption post not stored yet (resumable)",
    "check": "fetch the sitemap and any new, edited or current-week posts",
    "reparse": "re-run parse + upsert over stored versions (no network)",
    "report": "print per-post outage counts, warnings, failures and duplicates",
    "serve": "run checks at 07:00 and 17:00 Asia/Manila (catch-up on start)",
}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="outages", description="Cebu Brownout Tracker")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")
    for name, help_text in COMMANDS.items():
        sub.add_parser(name, help=help_text, description=help_text)
    return p


def main(argv: list[str] | None = None) -> int:
    """Run one command; return 0 ok, 1 failures, 2 usage."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = _parser().parse_args(argv)
    print(f"error: {args.command} is not implemented yet", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
