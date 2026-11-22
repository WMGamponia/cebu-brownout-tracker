"""The admin token the tray reads to control `serve` (tray spec 5.1)."""

from __future__ import annotations

from outages import admintoken


def test_write_then_read(tmp_path):
    token = admintoken.write_new(tmp_path)
    assert admintoken.read(tmp_path) == token
    assert len(token) == 43
    assert (tmp_path / admintoken.TOKEN_FILE).is_file()


def test_new_token_each_start(tmp_path):
    assert admintoken.write_new(tmp_path) != admintoken.write_new(tmp_path)


def test_read_missing(tmp_path):
    assert admintoken.read(tmp_path) is None


def test_write_creates_data_dir(tmp_path):
    token = admintoken.write_new(tmp_path / "data")
    assert admintoken.read(tmp_path / "data") == token
