"""`.env` lookup and Telegram settings (Phase 3 spec 5.9)."""

from __future__ import annotations

import pytest

from outages import config

TOKEN = "123456:SECRET-token-value"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_IDS", "TELEGRAM_ADMIN_CHAT_ID", "X_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)


def test_run_times_and_grace():
    assert config.RUN_TIMES == ("06:00", "20:00")
    assert config.MISFIRE_GRACE_S == 6 * 3600
    assert not hasattr(config, "CHECK_TIMES")


def test_dotenv_get_env_wins(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("X_KEY=file\n", encoding="utf-8")
    monkeypatch.setenv("X_KEY", "env")
    assert config.dotenv_get("X_KEY") == "env"


def test_dotenv_get_reads_file(tmp_path):
    (tmp_path / ".env").write_text("# c\nOTHER=1\n X_KEY = 'quoted' \n", encoding="utf-8")
    assert config.dotenv_get("X_KEY") == "quoted"
    assert config.dotenv_get("MISSING") is None


def test_telegram_settings_none_without_token():
    assert config.telegram_settings() is None


def test_telegram_settings_parses_ids(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123, -456")
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "123")
    s = config.telegram_settings()
    assert s is not None
    assert s.token == TOKEN
    assert s.allowed == frozenset({123, -456})
    assert s.admin_chat_id == 123


def test_telegram_settings_optional_ids(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    s = config.telegram_settings()
    assert s is not None and s.allowed == frozenset() and s.admin_chat_id is None


def test_bad_chat_id_error_hides_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "12, abc")
    with pytest.raises(ValueError) as exc:
        config.telegram_settings()
    assert "TELEGRAM_ALLOWED_CHAT_IDS" in str(exc.value)
    assert TOKEN not in str(exc.value) and "SECRET" not in repr(exc.value)


def test_settings_repr_hides_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    assert "SECRET" not in repr(config.telegram_settings())


def test_map_settings():
    assert config.MAP_HOST == "127.0.0.1"
    assert config.MAP_PORT == 8765
    assert config.STALE_CHECK_H == 15
    assert set(config.MECO_TOWNS.values()) == {"731100000", "702223000"}
    assert not set(config.MECO_TOWNS.values()) & set(config.VECO_TOWNS.values())
