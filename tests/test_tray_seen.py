"""Notifications already sent survive a tray restart (tray spec 5.4)."""

from __future__ import annotations

from outages.tray.seen import Seen
from outages.tray.state import Event


def ev(key: str) -> Event:
    return Event(key, f"text {key}")


def test_fresh_once(tmp_path):
    seen = Seen(tmp_path / "tray_state.json")
    seen.load()
    assert seen.fresh([ev("a"), ev("b")]) == [ev("a"), ev("b")]
    assert seen.fresh([ev("a"), ev("c")]) == [ev("c")]


def test_survives_reload(tmp_path):
    path = tmp_path / "tray_state.json"
    first = Seen(path)
    first.load()
    first.fresh([ev("a")])
    again = Seen(path)
    again.load()
    assert again.fresh([ev("a"), ev("b")]) == [ev("b")]


def test_missing_file_is_empty(tmp_path):
    seen = Seen(tmp_path / "nope" / "tray_state.json")
    seen.load()
    assert seen.fresh([ev("a")]) == [ev("a")]
    assert (tmp_path / "nope" / "tray_state.json").is_file()


def test_seen_corrupt_file(tmp_path):
    path = tmp_path / "tray_state.json"
    path.write_text("{not json", encoding="utf-8")
    seen = Seen(path)
    seen.load()
    assert seen.fresh([ev("a")]) == [ev("a")]
    again = Seen(path)
    again.load()
    assert again.fresh([ev("a")]) == []


def test_wrong_shape_is_empty(tmp_path):
    path = tmp_path / "tray_state.json"
    path.write_text('{"seen": 5}', encoding="utf-8")
    seen = Seen(path)
    seen.load()
    assert seen.fresh([ev("a")]) == [ev("a")]


def test_cap_500(tmp_path):
    path = tmp_path / "tray_state.json"
    seen = Seen(path)
    seen.load()
    seen.fresh([ev(f"k{i}") for i in range(600)])
    again = Seen(path)
    again.load()
    assert again.fresh([ev("k599")]) == []  # newest kept
    assert again.fresh([ev("k0")]) == [ev("k0")]  # oldest dropped
