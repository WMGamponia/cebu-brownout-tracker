# Cebu Brownout Tracker

Turns Visayan Electric (VECO) scheduled power interruption posts into data, per-barangay Telegram
alerts and (later) a map. Runs on one PC: Python, httpx + selectolax, SQLite, APScheduler.
Plans, decisions and progress live in [`ROADMAP.md`](ROADMAP.md); designs in
[`docs/superpowers/specs/`](docs/superpowers/specs/).

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows; .venv/bin/... elsewhere
python -m pytest -q && ruff check && ruff format --check
```

## Commands

`python -m outages <command>`; `--help` lists them all.

| Command | Network | Does |
|---|---|---|
| `backfill` / `check` | VECO | fetch interruption posts (polite: sitemap, one request at a time) |
| `reparse`, `report`, `areas` | none | re-parse stored posts, print the report, parse area texts |
| `alerts --dry-run` | none | print what an alert run would send now; read-only |
| `alerts --send` | Telegram | one alert run on stored data (no VECO fetch) |
| `bot` | Telegram | the subscription bot, until Ctrl+C |
| `serve` | VECO + Telegram | check + alerts at 06:00 and 20:00 Manila, catch-up on start, bot thread |

## Telegram alerts

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token it gives you.
2. Create `.env` in the project root (git ignores it; never commit or paste it):
   ```
   TELEGRAM_BOT_TOKEN=<token from BotFather>
   TELEGRAM_ALLOWED_CHAT_IDS=
   TELEGRAM_ADMIN_CHAT_ID=
   ```
3. Run `python -m outages bot` and send your bot `/start`. You aren't allowed yet, so it answers
   "This is a private bot." once and logs `bot: message from chat <id> (not allowed)`.
4. Put that ID in both keys (`TELEGRAM_ALLOWED_CHAT_IDS` takes several, comma-separated), restart
   the bot, send `/start` and `/add <your barangay>` (e.g. `/add Lahug`; a name in several towns
   gives buttons). `/list` and `/remove` manage subscriptions.
5. `python -m outages alerts --dry-run` shows what would be sent; `serve` sends it on schedule.

Alerts go out the evening before (~20:00) and the morning of (~06:00) an outage, and when VECO
adds, cancels, re-times, re-scopes or removes one. A row whose barangay VECO lists under the wrong
town never alerts anyone; the admin chat gets a note with the fix instead. The token never
appears in logs or errors.
