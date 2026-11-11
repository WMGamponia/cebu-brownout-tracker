# Phase 3 Telegram Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `serve` runs a check and then an alert run at 06:00 and 20:00 Manila, sending each allowlisted Telegram subscriber one "may affect you" message about outages in their barangays (day before, day of, and changes), never twice, with an offline `alerts --dry-run` and a polling bot for `/start`, `/add`, `/list`, `/remove`.

**Architecture:** A pure `alerts.select` turns a DB snapshot into due `AlertItem`s, a pure `alerts.render` turns them into Telegram HTML, and `alerts.run` sends through a `Notifier` and records each item in `alerts_sent` only after Telegram accepts it. `telegram.py` is a four-call Bot API client over httpx; `bot.py` handles updates in a daemon thread inside `serve`. The scheduler's two jobs replace the 07:00/17:00 checks.

**Tech Stack:** Python 3.12+ (dev 3.14), stdlib `sqlite3`, `html`, `threading`; httpx (already a dependency); APScheduler 3.x; Telegram Bot API (HTML parse mode, `getUpdates` long polling). pytest with `httpx.MockTransport`, ruff, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-11-phase3-alerts-design.md` (read it first; § numbers below refer to it).

## Global Constraints

- **No new dependencies.** Telegram over httpx. httpx may now also be imported by `telegram.py`.
- **No requests to visayanelectric.com during the build.** `alerts --dry-run`, `alerts --send`, `bot` and `report` never build the VECO fetcher; only `check`, `backfill` and `serve` do. Every new offline command gets a `no_network` CLI test (`tests/test_cli.py`).
- **Telegram:** no real call in CI; mocks use `httpx.MockTransport`. The one live test is marked `live_telegram`; `addopts` becomes `-m 'not live and not llm and not live_telegram'`. **The first real Telegram message waits for the owner's OK** (Task 9).
- **Token safety:** `TELEGRAM_BOT_TOKEN` comes only from the environment or `.env`; it never appears in logs, exceptions, CLI output or commits.
- `.env` keys: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS` (comma-separated integers), `TELEGRAM_ADMIN_CHAT_ID` (integer).
- Times: outage times ISO 8601 `+08:00`; bookkeeping UTC ISO 8601 (`timespec="seconds"`). Due times are computed in `timeparse.MANILA`, never the machine's local zone.
- Copy fixed by the spec: header `Scheduled outages that may affect you`; sections `Changes`, `Today`, `Tomorrow`; removed → `no longer listed in VECO's post`; failed-parse caveat `VECO updated this post and we couldn't read the update; this is from the earlier version.`; ambiguous note `VECO lists <name> under <Town A> and <Town B>.`; stranger reply `This is a private bot.`; up to 5 entities then `+N more`.
- Files read/written with `encoding="utf-8"`, `newline="\n"`; CLI output must not crash on a cp1252 console.
- Before any write to the real `data/outages.db` (`areas --rebuild`, migration 003 on first `connect`), back it up (`data/outages.db.bak-<date>`) and run the read-only dry run first.
- `data/` and `.env` are never committed. Commits: conventional with a scope (`feat(alerts): …`), imperative, under 72 chars. **No `Co-Authored-By` or "Generated with" lines.** No push, PR or merge without the owner's OK.
- Branch `phase3-alerts` (exists; spec committed as `5ab6b97`). Never leave an empty `python -` heredoc; prefer the Edit tool on Windows.

## Review Focus

1. **A message longer than Telegram's 4,096-character limit** (a busy barangay, several subscriptions, a full week's post): split at line boundaries into several messages, record the items only when every part is sent → Task 4 `test_render_splits_under_4096`, Task 6 `test_partial_send_records_nothing`.
2. **One outage reached through two subscriptions** (subscribed to both Tayud rows, or to two barangays in one outage): one line in the message, one ledger row → Task 3 `test_two_subscriptions_one_item`.
3. **Updates the bot doesn't handle** (stickers, photos, `edited_message`, group chats, `my_chat_member`): ignored without error, and the offset still advances so they aren't fetched forever → Task 7 `test_unhandled_updates_ignored_offset_advances`.
4. **`alerts --dry-run` on a DB without migration 003** (first run after pulling): prints `no subscribers (migration 003 not applied)` and leaves the file unchanged, never migrates → Task 6 `test_dry_run_on_unmigrated_db`.
5. **The bot thread and a scheduled run writing at the same time** (a `/add` during a slow areas step): `db.connect` sets `PRAGMA busy_timeout=30000`, so the write waits instead of raising `database is locked` → Task 1 `test_connect_sets_busy_timeout`.

---

### Task 1: Foundation — migration, config, subscriber store

**Files:**
- Create: `src/outages/migrations/003_alerts.sql` (spec §5.1 verbatim), `src/outages/alerts/__init__.py`, `src/outages/alerts/store.py`
- Modify: `src/outages/db.py` (`connect`: `PRAGMA busy_timeout=30000`), `src/outages/config.py`, `src/outages/areas/llm.py` (use `config.dotenv_get`), `pyproject.toml` (marker + addopts)
- Test: `tests/test_db.py`, `tests/test_config.py`, `tests/test_alerts_store.py`

**Interfaces:**
- Produces (`config.py`): `RUN_TIMES = ("06:00", "20:00")` (replaces `CHECK_TIMES`); `MISFIRE_GRACE_S = 6 * 3600`; `dotenv_get(name: str, path: Path = Path(".env")) -> str | None` (environment first, then `.env`); `@dataclass(frozen=True) TelegramSettings(token: str, allowed: frozenset[int], admin_chat_id: int | None)`; `telegram_settings() -> TelegramSettings | None` (None without a token; `ValueError` naming the key on a non-integer chat ID, never echoing the token).
- Produces (`alerts/store.py`): `@dataclass(frozen=True) Subscriber(id: int, chat_id: int, display_name: str | None, active: bool)`; `upsert_subscriber(conn, chat_id: int, display_name: str | None, now: datetime) -> Subscriber` (creates or reactivates); `set_active(conn, subscriber_id: int, active: bool)`; `subscribe(conn, subscriber_id, psgc_code, now) -> bool` (False if already); `unsubscribe(conn, subscriber_id, psgc_code) -> bool`; `subscriptions(conn, subscriber_id) -> list[str]`; `note_sent(conn, key: str) -> bool`; `mark_note(conn, key: str, now)`.

- [ ] **Step 1: Write the failing tests**
  - `test_migration_003_tables` — after `db.connect(tmp)`, `subscribers`, `subscriptions`, `alerts_sent`, `admin_notes_sent` exist and `schema_version` max is 3.
  - `test_alerts_sent_unique_item` — inserting `(1, 7, 'change', 5)` twice raises `sqlite3.IntegrityError`; `(1, 7, 'day_of', NULL)` twice also raises.
  - `test_connect_sets_busy_timeout` — `conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000`.
  - `test_dotenv_get_env_wins` / `test_dotenv_get_reads_file` / `test_telegram_settings_none_without_token` / `test_telegram_settings_parses_ids` (`"123, -456"` → `frozenset({123, -456})`) / `test_bad_chat_id_error_hides_token`.
  - `test_upsert_subscriber_reactivates`, `test_subscribe_idempotent`, `test_unsubscribe`.
- [ ] **Step 2: Run** `pytest tests/test_db.py tests/test_config.py tests/test_alerts_store.py -v` → FAIL (missing tables/functions).
- [ ] **Step 3: Implement** the migration, `busy_timeout`, config additions, `alerts/store.py`; replace `llm._dotenv_key` with `config.dotenv_get`; add marker `live_telegram: sends a real Telegram message; run with -m live_telegram`.
- [ ] **Step 4: Run** `pytest -q` → all pass (existing `CHECK_TIMES` users updated to `RUN_TIMES`).
- [ ] **Step 5: Commit** `feat(alerts): migration 003, Telegram settings and subscriber store`

### Task 2: Gazetteer — missing barangays and search

**Files:**
- Create: `src/outages/areas/barangays_extra.csv` (columns `psgc_code,name,town,town_psgc,source`)
- Modify: `src/outages/areas/gazetteer.py`, `evals/areas/results/2026-11-11-test.md` (footnote)
- Test: `tests/test_gazetteer.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) Place(code: str, name: str, town: str, town_psgc: str)`; `Gazetteer.place(code: str) -> Place | None`; `Gazetteer.search(query: str) -> list[Place]` — normalized exact name match across all VECO towns, plus alias targets (any town), deduped, sorted by town then name; `load()` now merges `barangays_extra.csv`.

- [ ] **Step 1: Look up** Lipata (Minglanilla) and Santo Niño (Cebu City) PSGC codes on psa.gov.ph (not VECO). Map to the release's 9-digit form (e.g. Cebu City barangays are `7306000NN`). If a code can't be mapped, use `<town prefix>9NN` with `source=local`. Write the CSV with `source=psa` or `local`. **Stop and show the owner** the two rows and their source.
- [ ] **Step 2: Write the failing tests**
  - `test_extra_rows_resolve_exact` — `resolve(segment("Portion of Minglanilla (Lipata)"))` gives one `exact` match with the CSV's code; same for `Sto. Niño`, Cebu City.
  - `test_search_two_towns` — `search("San Roque")` returns ≥ 2 `Place`s in different towns; `search("tayud")` returns Consolacion and Liloan.
  - `test_search_alias` — `search("Duljo Fatima")` returns `Place("730600027", "Duljo", "Cebu City", …)`.
  - `test_search_unknown_empty` — `search("Atlantis") == []`.
- [ ] **Step 3: Run** `pytest tests/test_gazetteer.py -v` → FAIL.
- [ ] **Step 4: Implement** `Place`, `place`, `search`, the merge in `load()`.
- [ ] **Step 5: Run** `pytest -q` → pass (the frozen eval regression test must still pass; if a coverage number moves, report it, don't edit labels).
- [ ] **Step 6: Dry run on the real DB:** `python -m outages areas --dry-run` → unmatched drops by 21 (Lipata 11, Sto. Niño 10), nothing else moves. **Show the owner; with their OK**, back up the DB and run `python -m outages areas --rebuild`.
- [ ] **Step 7:** Add the footnote to the Phase 2 test report: gazetteer extras added 2026-11-11 change coverage, not the frozen scores. **Commit** `feat(areas): add Lipata and Sto. Niño; gazetteer search`

### Task 3: `alerts/select.py` — due items

**Files:**
- Create: `src/outages/alerts/select.py`
- Test: `tests/test_alerts_select.py`

**Interfaces:**
- Consumes: `Gazetteer.place`, `segment.segment`, `Gazetteer.resolve` (old areas), `timeparse.MANILA`.
- Produces:
  - `@dataclass(frozen=True) Sub(subscriber_id: int, chat_id: int, psgc_code: str, created_at: datetime)`
  - `@dataclass(frozen=True) Outage(id: int, post_url: str, start_at: datetime, end_at: datetime, status: str, purpose: str, areas_hash: str | None, post_failed: bool)`
  - `@dataclass(frozen=True) Row(psgc_code: str | None, name_raw: str, town_psgc: str | None, match: str)`
  - `@dataclass(frozen=True) Change(id: int, outage_id: int, kind: str, old: dict | None, new: dict | None, detected_at: datetime, old_codes: frozenset[str])`
  - `@dataclass(frozen=True) Snapshot(subs: list[Sub], outages: dict[int, Outage], rows: dict[str, list[Row]], entities: dict[str, list[str]], changes: list[Change], sent: frozenset[tuple[int, int, str, int]])` — `sent` keys `(subscriber_id, outage_id, kind, change_id or 0)`
  - `@dataclass(frozen=True) AlertItem(subscriber_id: int, chat_id: int, psgc_code: str, outage_id: int, kind: Literal["day_before", "day_of", "change"], change_id: int | None = None, change_kind: str | None = None, direction: Literal["in", "out"] | None = None)`
  - `ALERT_MATCHES = frozenset({"exact", "alias", "ambiguous"})`
  - `load(conn, now: datetime, gaz: Gazetteer) -> Snapshot` — active subscribers only; outages with `end_at ≥ now − 1 day`; `post_failed` = the post's `parse_status = 'failed'`; `old_codes` from resolving `old["areas_raw"]` for `areas_changed`; returns an empty `Snapshot` when `subscribers` doesn't exist.
  - `due_items(snap: Snapshot, now: datetime) -> list[AlertItem]` — pure, deduped per `(subscriber_id, outage_id, kind, change_id)`; ordered by start time.

- [ ] **Step 1: Write the failing tests** — a table-driven `test_due` over a builder that makes a one-outage `Snapshot`. Use `now` in Manila; cases (expected kinds):
  - outage 2026-11-13 09:00; now 11-12 19:59 → `[]`; now 11-12 20:00 → `[day_before]`; now 11-13 05:59 → `[day_before]`; now 11-13 06:00 → `[day_of]` (day_before dropped); same with day_before already sent → `[day_of]`; now 11-13 09:00 → `[]` (started).
  - outage 11-13 05:00: now 11-12 20:00 → `[day_before]`; now 11-13 06:00 → `[]`.
  - outage 11-13 22:00, day_of not sent: now 11-13 20:00 → `[day_of]`.
  - status `cancelled` / `removed` → no reminder.
  - match `other_town` / `unmatched` → `[]`; `ambiguous` → item.
  - `test_change_items` — `time_changed` detected after `created_at` → change item; detected before → none; outage ended (both old and new `end_at` < now) → none.
  - `test_areas_changed_direction` — code only in `old_codes` → `direction="out"`; only in new rows → `"in"`; in neither → none.
  - `test_two_subscriptions_one_item` — subscriber subscribed to both Tayud codes, outage with both ambiguous rows → one `day_of` item.
  - `test_due_times_use_manila` — `now = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)` (= 20:00 Manila) gives `day_before` for an 11-13 outage, with `TZ` unaffected.
  - `test_load_resolves_old_areas` / `test_load_empty_without_migration` on a tmp DB.
- [ ] **Step 2: Run** `pytest tests/test_alerts_select.py -v` → FAIL.
- [ ] **Step 3: Implement** per spec §4.1–4.2.
- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(alerts): select due alert items`

### Task 4: `alerts/render.py` — HTML messages

**Files:**
- Create: `src/outages/alerts/render.py`, `tests/golden/alerts/*.html`
- Test: `tests/test_alerts_render.py`

**Interfaces:**
- Consumes: `AlertItem`, `Snapshot`, `Gazetteer.place`.
- Produces: `MAX_LEN = 4096`; `render(items: list[AlertItem], snap: Snapshot, gaz: Gazetteer, now: datetime) -> list[str]` — one subscriber's items → one or more HTML messages, each `≤ MAX_LEN`, split only between outage lines; `render_upcoming(psgc_code, snap, gaz, now) -> str` (the `/add` reply: next 7 days or `No scheduled outages in the next 7 days.`).

- [ ] **Step 1: Write the failing tests**
  - `test_render_golden_mixed` — a change (time_changed), one Today, one Tomorrow → equals `tests/golden/alerts/mixed.html` (write the golden by hand from spec §4.4: section order, `Fri 13 Nov, 9:00 AM–5:00 PM`, `Portion of Lahug (Cebu City)`, streets, purpose, `<a href="…">VECO post</a>`).
  - `test_render_ambiguous_note`, `test_render_failed_parse_caveat`, `test_render_removed_wording` (exact copy from Global Constraints).
  - `test_change_and_reminder_one_line` — a `time_changed` item and a `day_of` item for the same outage → the outage appears once, under `Changes` (Task 6's run still records both items).
  - `test_render_plus_more` — 8 entities → 5 names then `+3 more`.
  - `test_render_escapes` — purpose `Line <upgrade> & repair` appears as `Line &lt;upgrade&gt; &amp; repair`.
  - `test_render_sections_by_date` — a `day_before` item rendered at 01:00 on the start date sits under `Today`.
  - `test_render_splits_under_4096` — 60 outages → every part `≤ 4096`, every outage appears exactly once across parts, each part valid (balanced tags).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(alerts): render alert messages as Telegram HTML`

### Task 5: `telegram.py` and `notify.py`

**Files:**
- Create: `src/outages/telegram.py`, `src/outages/notify.py`
- Test: `tests/test_telegram.py`, `tests/test_notify.py`

**Interfaces:**
- Produces (`telegram.py`): `API = "https://api.telegram.org"`; `class TelegramError(Exception)` with `status: int`, `description: str`, `retry_after: int | None` (its `str()` never contains the token); `class TelegramClient(token: str, transport: httpx.BaseTransport | None = None, timeout_s: float = 60)` with `get_updates(offset: int | None, timeout_s: int = 50) -> list[dict]`, `send_message(chat_id: int, text: str, html: bool = True, reply_markup: dict | None = None) -> dict`, `answer_callback_query(callback_id: str, text: str | None = None)`, `edit_message_reply_markup(chat_id: int, message_id: int, reply_markup: dict | None)`, `close()`; `install_redaction(token: str) -> None` (sets `httpx`/`httpcore` loggers to WARNING and adds a filter to the `outages` logger's handlers replacing the token with `<token>`).
- Produces (`notify.py`): `class SendError(Exception)` with `kind: Literal["retry", "blocked", "bad_request"]`; `class Notifier(Protocol): def send(self, chat_id: int, html: str) -> None`; `TelegramNotifier(client: TelegramClient, sleep=time.sleep)`; `PrintNotifier(stream=sys.stdout)`.

- [ ] **Step 1: Write the failing tests** (all with `httpx.MockTransport`)
  - `test_send_message_posts_html` — request path `/bot<TOKEN>/sendMessage`, JSON has `parse_mode="HTML"`, `disable_web_page_preview=True`.
  - `test_get_updates_offset_and_timeout`.
  - `test_error_has_no_token` — a 401 `TelegramError` whose `str()` and `repr()` lack the token.
  - `test_token_never_logged` — with `_setup_logging`-style file handler and `install_redaction`, a send and an error leave no token in the log file.
  - `test_notifier_429_waits_and_retries` (fake `sleep` gets `retry_after`; second call ok); `test_notifier_429_twice_is_retry`; `test_notifier_403_blocked`; `test_notifier_400_resends_plain` (second request has no `parse_mode`; if it also fails → `bad_request`); `test_notifier_5xx_and_network_retry`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(telegram): Bot API client over httpx and notifiers`

### Task 6: `alerts/run.py`, admin notes, `alerts` CLI, `report` lines

**Files:**
- Create: `src/outages/alerts/run.py`
- Modify: `src/outages/__main__.py` (`alerts` command), `src/outages/report.py`
- Test: `tests/test_alerts_run.py`, `tests/test_cli.py`, `tests/test_report.py`

**Interfaces:**
- Consumes: `select.load`, `select.due_items`, `render.render`, `Notifier`, `SendError`, `alerts.store`.
- Produces: `@dataclass(frozen=True) RunSummary(subscribers: int, sent: int, items: int, failed: int, notes: int)`; `alert_run(conn, notifier: Notifier, now: datetime, gaz: Gazetteer, admin_chat_id: int | None, check_ok: bool) -> RunSummary`; `admin_notes(conn, snap: Snapshot, now, check_ok: bool) -> list[tuple[str, str]]` (key, text; keys `parse_failed:<post_id>:<version_id>`, `unparsed:<areas_hash>`, `other_town:<areas_hash>:<seq>`, `unmatched:<areas_hash>:<seq>`, `check_failed:<YYYY-MM-DD HH>`, `bad_request:<subscriber_id>:<YYYY-MM-DD HH>`); `dry_run(conn, now, gaz) -> str`; `report.alerts_line(conn) -> str | None` (`alerts: N subscribers, M sent; unparsed areas: K upcoming outages`).
- CLI: `alerts --dry-run` (`db.connect_ro`, prints `dry_run`), `alerts --send` (needs `telegram_settings()`, else exit 1 with `error: TELEGRAM_BOT_TOKEN not set`).

- [ ] **Step 1: Write the failing tests** (in-memory/tmp DB seeded through `db.apply_changes` and `areas_store.write_parse`; `RecordingNotifier` in the test file)
  - `test_second_run_sends_nothing`.
  - `test_retry_failure_unrecorded_then_sent` — first run raises `SendError("retry")`, no `alerts_sent` rows; second run sends.
  - `test_partial_send_records_nothing` — part 2 of a split message fails → no rows for any of its items.
  - `test_blocked_deactivates`.
  - `test_admin_notes_once_per_key` — a failed post, an unparsed hash, an `other_town` row → three notes to `admin_chat_id`; second run sends none.
  - `test_check_failed_note` — `check_ok=False` → note with `last_check_at`.
  - `test_run_never_builds_fetcher` and, in `test_cli.py`, `test_alerts_dry_run_offline` (uses `no_network`, also patches the Telegram client to fail if built; DB file hash unchanged) and `test_report_offline_builds_no_telegram`.
  - `test_dry_run_on_unmigrated_db` — a v2 DB → output `no subscribers (migration 003 not applied)`, file hash unchanged.
  - `test_alerts_send_without_token_exits_1` (output lacks any token-like string).
  - `test_report_alerts_line`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (record items and `mark_note` in one transaction after the last part is accepted). **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(alerts): alert run, admin notes and alerts command`

### Task 7: `bot.py` and the `bot` command

**Files:**
- Create: `src/outages/bot.py`
- Modify: `src/outages/__main__.py` (`bot` command)
- Test: `tests/test_bot.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `alerts.store`, `Gazetteer.search/place/suggest`, `render.render_upcoming`, `TelegramClient`.
- Produces: `@dataclass(frozen=True) Reply(chat_id: int, text: str, markup: dict | None = None, callback_id: str | None = None, edit_message_id: int | None = None)`; `handle(update: dict, conn, gaz: Gazetteer, allowed: frozenset[int], now: datetime) -> list[Reply]`; `deliver(client: TelegramClient, replies: list[Reply])`; `poll_once(client, conn, gaz, allowed, now) -> int` (number of updates; stores `meta.telegram_update_offset = last update_id + 1`); `poll_forever(client, db_path: Path, gaz, allowed, stop: threading.Event, sleep=time.sleep)` (own connection; errors logged, backoff 5 s doubling to 60 s, reset after a good poll).
- Callback data: `add:<psgc_code>`, `rm:<psgc_code>`.

- [ ] **Step 1: Write the failing tests**
  - `test_stranger_gets_one_reply` — two messages from a non-allowed chat → one `This is a private bot.` total, and one INFO log line `bot: message from chat <id> (not allowed)` (how the owner finds their chat ID; never the token).
  - `test_start_creates_subscriber`; `test_add_single_match_subscribes_and_lists_upcoming`; `test_add_two_towns_gives_buttons` (`/add San Roque` → `markup` with one button per town, `callback_data` `add:<code>`); `test_callback_add_subscribes_and_answers`; `test_add_unknown_suggests`; `test_add_without_name_shows_usage`; `test_list`; `test_remove_buttons_and_callback`.
  - `test_unhandled_updates_ignored_offset_advances` — sticker, `edited_message`, group-chat message, `my_chat_member` → no replies, no error, offset = last id + 1 (MockTransport).
  - `test_poll_forever_backs_off_and_stops` — transport raises twice; fake `sleep` gets 5, 10; `stop` set → returns.
  - `test_bot_command_offline` in `test_cli.py` (`no_network`; Telegram client patched to a MockTransport).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(bot): allowlisted subscription bot with long polling`

### Task 8: `serve` wiring

**Files:**
- Modify: `src/outages/scheduler.py`, `src/outages/__main__.py`, `src/outages/pipeline.py` (only if `check` must expose `check_ok`; prefer `summary.errors == 0` in the CLI)
- Test: `tests/test_scheduler.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `pipeline.check`, `alerts.run.alert_run`, `bot.poll_forever`, `config.RUN_TIMES`, `config.MISFIRE_GRACE_S`, `telegram_settings`.
- Produces: `scheduler.build(run: Callable[[], None]) -> BlockingScheduler` (jobs `run-06:00`, `run-20:00`, `coalesce=True`, `max_instances=1`, `misfire_grace_time=MISFIRE_GRACE_S`); `__main__._scheduled_run(paths, settings: TelegramSettings | None)` — check, then (with settings) `alert_run`; exceptions in either are logged, an alert run still happens after a failed check.

- [ ] **Step 1: Write the failing tests**
  - `test_scheduler_jobs_at_run_times` (two jobs, Manila, grace 21600).
  - `test_scheduled_run_alerts_after_failed_check` (check raises → `alert_run` called with `check_ok=False`).
  - `test_serve_without_token_checks_only` (log contains `alerts disabled`; no bot thread).
  - `test_serve_starts_bot_thread_and_catch_up` (patched scheduler `start` returns at once; catch-up calls `_scheduled_run` once when `last_check_at` is old; the bot thread is a daemon and `stop` is set on exit).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**; update `COMMANDS["serve"]` help to `run check + alerts at 06:00 and 20:00 Asia/Manila (catch-up on start)`. **Step 4: Run** `pytest -q && ruff check && ruff format --check` → clean.
- [ ] **Step 5: Commit** `feat(serve): check and alert runs at 06:00 and 20:00 with bot thread`

### Task 9: Real-data dry run and first live message (owner gates)

- [ ] **Step 1:** Back up `data/outages.db`. Run `python -m outages alerts --dry-run` (read-only; on an unmigrated DB it says so). Then `python -m outages report` (this migrates to 003 — fine after the backup).
- [ ] **Step 2: Stop for the owner:** they create the bot with BotFather, put `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`, `TELEGRAM_ADMIN_CHAT_ID` in `.env` (how to find the chat ID: send `/start`, then the `bot` command logs the stranger's chat ID once without the token). Do not read `.env` aloud or print the token.
- [ ] **Step 3: With the owner's OK:** `python -m outages bot`; owner sends `/start`, `/add <their barangay>`; Ctrl+C. Then `python -m outages alerts --dry-run` and show the output.
- [ ] **Step 4: With the owner's OK:** `python -m outages alerts --send` → owner confirms receipt; run it again → `sent 0`.
- [ ] **Step 5:** Optional `pytest -m live_telegram` (owner's OK). Record what was sent (counts only) in the ROADMAP.

### Task 10: Docs, review, PR ask

**Files:** `ROADMAP.md`, `README.md` (or the README's setup section if it exists: creating the bot, `.env` keys, `serve`)

- [ ] **Step 1:** ROADMAP: Stack row `Alerts | Telegram Bot API over httpx`; Polling line → two runs a day, ~4–6 VECO requests; replace the lesson "Phase 3 must not alert from a post whose latest parse failed" with "never present stale data as current"; Known issues: `other_town` handled by admin note + alias, Lipata / Sto. Niño resolved; tick Phase 3 items; progress entry.
- [ ] **Step 2:** `pytest -q`, `ruff check`, `ruff format --check` → green; then one whole-branch review with `superpowers:requesting-code-review`; fix findings.
- [ ] **Step 3:** Write `prompts/phase4.md` in the shape of `prompts/phase3.md`.
- [ ] **Step 4:** Ask the owner before pushing and opening the PR to `main`.
