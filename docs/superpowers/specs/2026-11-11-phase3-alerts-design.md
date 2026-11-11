# Phase 3 Telegram Alerts — Design

Status: approved in brainstorming 2026-11-11, section by section. Base: `main` at `3f0f0fd` (Phase 2 merged, PR #2).

## 1. Intent

Phase 3 sends a subscriber a Telegram message about outages that **may affect** their barangay:
- the evening before (~20:00 Manila) and the morning of (~06:00), and
- when VECO adds, removes, cancels, re-times or re-scopes an outage.

Each alert run first does a fresh `check`, so alerts come from the post as it is now. A `serve` that was off or asleep catches up on start. One subscriber now (the owner); the tables are multi-user so Phase 5 only lifts an allowlist.

Success:
- With `serve` running, a subscriber gets one message per run that has something for them, never the same item twice (also across restarts), and never an alert for an outage that has already started.
- No alert comes from a row that names the wrong town (`other_town`); bad or stale data is flagged, never presented as current.
- `alerts --dry-run` prints exactly what a run would send, offline. CI makes no Telegram or VECO request.
- VECO requests stay at ~4–6 a day.

## 2. Decisions (owner, 2026-11-11)

| Topic | Decision |
|---|---|
| Schedule | Two runs a day, **06:00 and 20:00** Manila. Each run is a full `check` (sitemap + current posts), then an alert run. They **replace** the 07:00 / 17:00 checks. ~4–6 VECO requests a day (a check is the sitemap + 1–2 current posts). Accepted cost: a change VECO makes between runs reaches the subscriber at the next run, so a same-day addition posted after 06:00 for that afternoon is missed. |
| Subscribing | A Telegram bot with `/start`, `/add <name>`, `/list`, `/remove`, long polling inside `serve`. Only chat IDs in `TELEGRAM_ALLOWED_CHAT_IDS` are served; others get one "private bot" reply. |
| Telegram client | The Bot API over **httpx** (no new dependency; `python-telegram-bot` would bring its own asyncio loop next to a synchronous `BlockingScheduler`). |
| Which rows alert | `exact`, `alias`, `ambiguous` alert (ambiguous with a note naming the towns VECO listed). `other_town` **never** alerts a subscriber (e.g. `Poblacion` under Minglanilla resolves to Liloan's and Talisay's Poblacion); it goes to the admin note with the fix (a town-scoped alias). Remaining `unmatched` rows go to the admin note too. |
| Missing barangays | `Lipata` (Minglanilla) and `Sto. Niño` (Cebu City) are added from a committed `barangays_extra.csv` with their PSA PSGC codes, merged by `gazetteer.load`; then `areas --rebuild`. |
| Failed parse | Outages from a post whose latest version failed to parse **still alert**, with a caveat ("VECO updated this post and we couldn't read the update; this is from the earlier version") and the post link; the admin chat gets the parse error. Replaces the Phase 2 lesson "don't alert from a failed post" with "never present stale data as current". |
| Unparsed areas | An alert run **warns, never refuses**: it sends everything it can and the admin chat gets "N upcoming outages have unparsed areas; run `python -m outages areas`". `report` counts them. |
| Admin chat | `TELEGRAM_ADMIN_CHAT_ID` gets operational notes only (never alerts), each once per key. |
| Format | Telegram **HTML** parse mode (`html.escape` only; MarkdownV2 would need escaping of VECO's `.`, `-`, `(`). |
| Discord | Not in Phase 3. Receiving commands needs a WebSocket gateway (new dependency) or a public HTTPS endpoint. Sending sits behind a `Notifier` interface so an alert-only Discord webhook can be added later. |

## 3. Findings that shaped the design

From `data/outages.db` (read-only, 2026-11-11):
- **Start times:** 09:00 1,876, 08:00 522, 22:00 252, 07:00 56, **05:00 53**. A 06:00 alert is too late for 05:00 starts; only the evening alert covers them. A 22:00 start is "today" for the 20:00 run.
- **`outage_changes` is empty**: the backfill never logs a post's first parse, so how often VECO edits a post is unknown.
- **Barangay rows:** exact 4,408, alias 305, ambiguous 112, other_town 13, unmatched 23.
  - `other_town`: `Jagobiao` (VECO: Consolacion → Mandaue's, likely right), `Poblacion` (VECO: Minglanilla → Liloan's and Talisay's, wrong), `Sta. Cruz`, `Cadulawan`. About 8 outages in two years.
  - `unmatched`: `Lipata` 11 outages, `Sto. Niño` 10; three one-off typos.
- **The areas step deletes orphaned `area_parses`**, so the barangays of an `areas_changed` change's *old* text may no longer be stored.
- **httpx logs request URLs at INFO**, and a Bot API URL contains the token (`/bot<token>/…`).
- Phase 1 already has an unused `outage_changes.notified_at`; this design uses a per-subscriber ledger instead and leaves that column alone.

## 4. The alert model

### 4.1 Items
An alert run builds **items** per (subscriber, subscribed barangay):

| kind | for | due at |
|---|---|---|
| `day_before` | a `scheduled`/`additional` outage | 20:00 on the day before its start date |
| `day_of` | a `scheduled`/`additional` outage starting at or after 06:00 | 06:00 on its start date |
| `change` | one `outage_changes` row | when detected |

Rules:
- Nothing is sent for an outage with `start_at ≤ now`.
- An outage starting before 06:00 (53 so far, mostly 05:00) has no `day_of`; its only reminder is `day_before`.
- When `day_of(o)` is due, an unsent `day_before(o)` is dropped (waking at 07:00 gives one "today" alert, not yesterday's "tomorrow" as well).
- Sections in the message ("Today", "Tomorrow") come from the start date relative to `now`, not from the item kind: a `day_before` sent at 01:00 after a wake-up shows under "Today".

A run sends every item that is **due, not in the ledger, and whose outage hasn't started** (for changes: hasn't ended, using the later of the old and new `end_at`).

### 4.2 Which outages concern a subscription
An outage concerns a subscription to `psgc_code` P when its `areas_hash` has an `area_barangays` row with `psgc_code = P` and `match ∈ (exact, alias, ambiguous)`.

For a change:
- `added`, `cancelled`, `time_changed`, `status_changed`, `removed`: the outage's current areas (as above).
- `areas_changed`: P is in the old **or** the new areas. The old text's barangays come from resolving `old_json.areas_raw` on the fly with the segmenter and resolver (pure, deterministic; only barangays are needed). The message says whether the barangay was added to or dropped from the outage.
- A change detected before the subscription's `created_at` is never sent (subscribing doesn't replay history).

### 4.3 Ledger and idempotency
`alerts_sent` holds one row per sent item, written **only after Telegram accepts the message**. A failed send leaves its items unrecorded, so the next run retries them. A crash between send and record gives one duplicate: accepted (a duplicate is better than a miss).

### 4.4 Grouping and wording
One message per subscriber per run, in sections: **Changes**, **Today**, **Tomorrow**. An outage appears once per message: if it has a change item and a due reminder in the same run, it is shown under Changes and both items are recorded.

Each outage line: date and time range, barangay (and town), "portion of", up to 5 streets/sitios from `area_entities` then "+N more", purpose, and a link to the post. Header: "Scheduled outages that may affect you". Notes where they apply:
- `ambiguous`: "VECO lists Tayud under Consolacion and Liloan."
- failed parse caveat (§2).
- `removed`: "no longer listed in VECO's post" (not "cancelled").

Message text is built with `html.escape`; links are `<a href>` to the post URL.

### 4.5 Catch-up and sleep
On start, `serve` runs a check if the last is older than `CATCH_UP_AFTER_H` (12 h, as now), then one alert run; §4.1's rule makes that the catch-up. The scheduler's `misfire_grace_time` goes from 1 h to **6 h**, `coalesce=True`, so a PC that slept through 20:00 and wakes at 23:00 still runs it once.

### 4.6 Not in scope
Quiet hours, per-user alert times, town-level subscriptions, alerts for non-subscribed barangays, any interaction beyond the four commands.

## 5. Components

```
src/outages/
  migrations/003_alerts.sql
  telegram.py        # Bot API client over httpx
  notify.py          # Notifier protocol: TelegramNotifier, PrintNotifier
  bot.py             # command handling + polling thread
  alerts/
    __init__.py
    select.py        # pure: due items
    render.py        # pure: items -> HTML message
    run.py           # select, render, send, record; admin notes
  areas/barangays_extra.csv
```

### 5.1 Storage (`003_alerts.sql`)
```sql
CREATE TABLE subscribers (
    id            INTEGER PRIMARY KEY,
    chat_id       INTEGER NOT NULL UNIQUE,
    display_name  TEXT,
    created_at    TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE subscriptions (
    subscriber_id  INTEGER NOT NULL REFERENCES subscribers(id),
    psgc_code      TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (subscriber_id, psgc_code)
);
CREATE TABLE alerts_sent (
    id             INTEGER PRIMARY KEY,
    subscriber_id  INTEGER NOT NULL REFERENCES subscribers(id),
    outage_id      INTEGER NOT NULL REFERENCES outages(id),
    kind           TEXT NOT NULL CHECK (kind IN ('day_before', 'day_of', 'change')),
    change_id      INTEGER REFERENCES outage_changes(id),
    sent_at        TEXT NOT NULL
);
CREATE UNIQUE INDEX alerts_sent_item
    ON alerts_sent(subscriber_id, outage_id, kind, coalesce(change_id, 0));
CREATE TABLE admin_notes_sent (
    key      TEXT PRIMARY KEY,   -- e.g. 'parse_failed:<post_id>:<version_id>'
    sent_at  TEXT NOT NULL
);
```
The `getUpdates` offset is kept in `meta` (`telegram_update_offset`). Times: UTC ISO 8601 bookkeeping, as in Phase 1.

### 5.2 `telegram.py`
`TelegramClient(token, transport=None)` over `httpx.Client`: `get_updates(offset, timeout_s=50)`, `send_message(chat_id, html, reply_markup=None)`, `answer_callback_query(id, text)`, `edit_message_reply_markup(chat_id, message_id, markup)`. Non-ok responses raise `TelegramError(status, description, retry_after)`.

**Token safety:** the token is read only from the environment or `.env`; the client sets the `httpx` and `httpcore` loggers to WARNING and adds a filter on the `outages` handlers that replaces the token with `<token>`. A test asserts the token never appears in the log file or in a raised error's text.

### 5.3 `notify.py`
`Notifier` protocol: `send(chat_id, html) -> None`, raising `SendError(kind)` with kind ∈ `retry`, `blocked`, `bad_request`. `TelegramNotifier` maps HTTP 429 (sleep `retry_after`, one retry), 403 → `blocked`, 400 → resend once as plain text then `bad_request`, 5xx/network → `retry`. `PrintNotifier` writes to stdout (dry run). Tests use a `RecordingNotifier`.

### 5.4 `alerts/select.py`
`due_items(now, subscriptions, outages, barangays, changes, sent) -> list[AlertItem]`: pure, no DB. Holds §4.1 and §4.2: due times, the started/ended cut-off, dropping `day_before`, the match policy, `areas_changed` old/new, the subscription `created_at` cut-off. A thin `load(conn, now)` reads the rows it needs (outages with start or end ≥ now − 1 day).

### 5.5 `alerts/render.py`
`render(items, now) -> str` (HTML), per §4.4. Golden files under `tests/golden/alerts/`.

### 5.6 `alerts/run.py`
`alert_run(conn, notifier, now, admin_chat_id, check_ok) -> RunSummary(sent, items, failed, notes)`. Per active subscriber: select → render → send → record in one transaction after the send. A `blocked` error sets `active = 0`. Then admin notes (each key once):
- a post with outages in the next 2 days whose latest parse failed;
- upcoming outages with no `area_parses` row;
- `other_town` / `unmatched` rows in upcoming outages (with the alias fix);
- a failed check (`check_ok=False`: "VECO not reachable; alerts from data as of <last_check_at>");
- a `bad_request` plain-text resend.

`alert_run` never fetches.

### 5.7 `bot.py`
- `handle(update, conn, gazetteer, allowed, now) -> list[Reply]` (pure except DB writes):
  - not allowed → one "This is a private bot." per chat (remembered in `admin_notes_sent` by chat ID), then silence.
  - `/start`: create or reactivate the subscriber; reply with help.
  - `/add <name>`: search the gazetteer (names, aliases, extra rows; `gazetteer.normalize_name`). One match → subscribe and reply with that barangay's upcoming outages (informational, not ledger items). Several towns (`San Roque`, `Banilad`) → inline buttons per town (`callback_data = add:<psgc_code>`). None → up to 3 `suggest()` names.
  - `/list`: subscriptions with town. `/remove`: buttons per subscription (`rm:<psgc_code>`).
- `poll_forever(client, db_path, stop_event)`: daemon thread with its own connection; long polls `getUpdates` (timeout 50 s), stores the offset after each batch; any error → log and back off 5 s doubling to 60 s; checks `stop_event` between polls.

### 5.8 Gazetteer
`barangays_extra.csv` (same columns as `barangays.csv` + `source`) holds Lipata and Sto. Niño with PSA PSGC codes in the release's 9-digit form. If PSA's code can't be mapped to that form, a local code `<town prefix>9xx` is used and marked `source=local`. `gazetteer.load()` merges it. After the change, `areas --rebuild`; the Phase 2 eval report gets a footnote that later gazetteer additions change the coverage numbers.

### 5.9 Config and scheduler
- `config.py`: `RUN_TIMES = ("06:00", "20:00")` replaces `CHECK_TIMES`; `MISFIRE_GRACE_S = 6 * 3600`; `dotenv_get(name)` moves here from `areas/llm.py` (`_dotenv_key`) and is used for `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS` (comma-separated), `TELEGRAM_ADMIN_CHAT_ID`.
- `scheduler.build(run)`: one job per `RUN_TIMES`, each calling `run()` = check then alert run.

## 6. CLI

`_run` keeps explicit dispatch and refuses unknown commands.

| Command | Network | Does |
|---|---|---|
| `alerts --dry-run` | none | Prints what a run would send now (per subscriber) and the admin notes; writes nothing. Opens the DB read-only. |
| `alerts --send` | Telegram only | One alert run on stored data (no VECO fetch). |
| `bot` | Telegram only | Polling only, to try the commands. |
| `serve` | VECO + Telegram | Catch-up check + alert run, scheduler at `RUN_TIMES`, bot thread. Without `TELEGRAM_BOT_TOKEN`: logs "alerts disabled" and runs checks only. |
| `report` | none | Adds "unparsed areas: N upcoming outages" and subscriber / alerts-sent counts. |

## 7. Error handling

| Failure | Behaviour |
|---|---|
| Check fails (VECO down) | The alert run still runs on stored data; admin note. |
| Telegram 429 | Wait `retry_after`, retry once; else the item stays unrecorded. |
| Telegram 5xx / network | Log; items stay unrecorded; next run retries. |
| Telegram 403 (user blocked the bot) | Subscriber `active = 0`; `/start` reactivates. |
| Telegram 400 (bad HTML) | Resend as plain text; admin note. |
| Failed post parse | Alerts with the caveat; admin note (§2). |
| Unparsed areas | Warn; admin note; `report` count. |
| Bot thread error | Logged, backoff 5 → 60 s; never stops `serve`. |
| No token | `serve` runs checks only; `alerts --send` / `bot` exit 1 with a message. |

## 8. Testing

No real Telegram or VECO call in CI; the Phase 2 suite-wide network block stays.
- **`select`**, table-driven with a fixed `now`: 05:59 / 06:00 / 19:59 / 20:00; a 22:00 outage (day_of at 06:00, still due at the 20:00 run if 06:00 was missed); a 05:00 outage (day_before only, shown under "Today" after midnight); waking at 07:00 drops day_before; started outage → nothing; cancelled outage → no reminder; each match kind; failed-parse caveat flag; `areas_changed` into and out of the barangay (old text resolved on the fly); a change before the subscription's `created_at`; a change to an ended outage.
- **`render`**: golden HTML for a mixed message, an ambiguous note, the caveat, "+N more"; escaping of real VECO text with `&`, `<`, `(`.
- **`run`** on an in-memory DB with a `RecordingNotifier`: a second run sends nothing; a `retry` failure leaves items unrecorded and the next run sends them; `blocked` deactivates; admin notes once per key; `alert_run` never builds a fetcher.
- **`telegram` / `bot`** with `httpx.MockTransport`: allowlist; `/add San Roque` gives town buttons; callback subscribes; offset advances; 429/403/400 mapping; the token is absent from the log file and error text.
- **CLI**: `alerts --dry-run` and `report` build no fetcher and no Telegram client; `alerts --dry-run` leaves the DB hash unchanged.
- **Live**: one opt-in `-m live_telegram` test (needs a token and `TELEGRAM_ADMIN_CHAT_ID`); never in CI.

## 9. Build order (for the plan)

1. Migration 003, `dotenv_get` move, config (`RUN_TIMES`, grace).
2. Gazetteer extra rows (PSA lookup), `areas --rebuild` dry run first.
3. `alerts/select.py` (TDD, the case table).
4. `alerts/render.py` + golden files.
5. `telegram.py` + `notify.py` (MockTransport, token redaction).
6. `alerts/run.py` + admin notes; `alerts --dry-run` / `--send`.
7. `bot.py` + `bot` command.
8. `serve` wiring: scheduler `RUN_TIMES`, catch-up, bot thread; `report` lines.
9. Real-DB `alerts --dry-run` shown to the owner → **owner's OK** → first real message.
10. ROADMAP, README notes on creating the bot and `.env`.

## 10. Definition of done
- All of §8 passes on Ubuntu and Windows CI; ruff clean.
- `alerts --dry-run` on the real DB shows plausible messages for the owner's barangay, reviewed by the owner.
- With the owner's OK: `/start`, `/add`, one real alert run received, a second run sends nothing.
- `serve` runs at 06:00 / 20:00 only; ROADMAP's budget line says ~4–6 requests a day.

## 11. Gates (ask the owner first)
- The first real Telegram message. The owner creates the bot with BotFather and puts the token in `.env`.
- Any VECO request (the build needs none).
- `areas --rebuild` on the real DB (after a backup and a dry run).
- Push, PR, merge.

## 12. Risks and accepted limits
- **Same-day changes between runs are late** (§2). If VECO turns out to post same-day additions often, add a midday check later; the change log will show it.
- **A crash between send and record duplicates one message** (§4.3).
- **"May affect" is broad**: a "portion of" a barangay alerts the whole barangay; streets narrow it in the text, not in who is alerted (street-level is Phase 5).
- **`other_town` rows need a manual alias** before they alert anyone; until then the admin note is the only signal.
- **The PC must be on**: an outage that starts while the PC is off gets no alert; catch-up only covers outages that haven't begun.
