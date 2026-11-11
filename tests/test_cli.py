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


def test_gazetteer_command_prints_counts(capsys, monkeypatch, tmp_path):
    from outages import gazetteer_build

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gazetteer_build, "run", lambda d: {"Cebu City": 80, "Liloan": 14})
    assert main(["gazetteer"]) == 0
    out = capsys.readouterr().out
    assert "Cebu City 80" in out and "total 94" in out


def test_unknown_command_exits_2(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["frobnicate"])
    assert exc.value.code == 2
