"""CLI argument handling."""

from __future__ import annotations

import pytest

from outages.__main__ import main


def test_help_lists_commands(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for cmd in ("backfill", "check", "reparse", "report", "serve"):
        assert cmd in out


def test_unknown_command_exits_2(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["frobnicate"])
    assert exc.value.code == 2
