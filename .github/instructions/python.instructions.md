---
applyTo: "**/*.py"
---

# Python

Python 3.14 in `.venv` (`requires-python >= 3.12`), package `outages` under `src/`, installed editable. Dependencies live in `pyproject.toml` only.

## Style
- `from __future__ import annotations` at the top of modules with signatures; full type hints on public functions.
- Every module starts with a docstring saying what it does and which spec section it implements (`(spec 6.3)`); public functions get a one- or two-line docstring with the contract.
- Lines up to 100 characters (ruff). Results are `@dataclass(frozen=True)` records; domain errors are small `Exception` subclasses with a docstring.
- Private helpers start with `_` and stay private.
- Only `fetch.py` imports httpx; only `scheduler.py` imports APScheduler. `normalize`, `timeparse`, `extract`, `parse` and `upsert` do no I/O.
- Paths are `pathlib.Path` from `config.Paths(root)`, never from the current file's location (except package data such as migrations).

## Time
- Outage times are aware `datetime`s in `ZoneInfo("Asia/Manila")`, stored as `isoformat()` (`2026-11-11T22:00:00+08:00`).
- Bookkeeping timestamps (`fetched_at`, `detected_at`) are UTC `isoformat(timespec="seconds")`.
- Code that depends on "now" takes `now` as an argument so tests can fix it.

## Files and I/O
- Always `encoding="utf-8"`; write text with `newline="\n"`.
- Write JSON with `ensure_ascii=False` and a stable key order.

## CLIs and output
- `main(argv) -> int` in `__main__.py` returns 0 ok, 1 failure, 2 usage.
- Plain `print` for results, errors to `stderr` prefixed `error:`. `main` reconfigures stdout/stderr with `errors="replace"` so barangay names like `Sto. Niño` don't crash a cp1252 console.

## Tests (pytest)
- One file per module: `tests/test_<module>.py`. Test names describe the behavior: `test_explicit_dates_roll_year`.
- Plain `assert`, `pytest.fixture`, `parametrize`, `tmp_path`, `monkeypatch`, `httpx.MockTransport` — no mocking library.
- Fixtures are **real posts** saved in `tests/fixtures/posts/`; synthetic HTML only for cases no real post shows yet.
- Mark network tests `@pytest.mark.live`; they never run in CI.
