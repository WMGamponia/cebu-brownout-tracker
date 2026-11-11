# Cebu Brownout Tracker

Turns Visayan Electric (VECO) scheduled power interruption posts into data, per-barangay Telegram
alerts and a local map. Runs on one PC: Python, httpx + selectolax, SQLite, APScheduler.
Plans, decisions and progress live in [`ROADMAP.md`](ROADMAP.md); designs in
[`docs/superpowers/specs/`](docs/superpowers/specs/).

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows; .venv/bin/... elsewhere
python -m pytest -q && ruff check && ruff format --check
node --test "tests/js/*.test.mjs"                 # the map's time logic; needs Node 22+
```

Node is only for those tests; the app itself needs no npm packages.

## Commands

`python -m outages <command>`; `--help` lists them all.

| Command | Network | Does |
|---|---|---|
| `backfill` / `check` | VECO | fetch interruption posts (polite: sitemap, one request at a time) |
| `reparse`, `report`, `areas` | none | re-parse stored posts, print the report, parse area texts |
| `alerts --dry-run` | none | print what an alert run would send now; read-only |
| `alerts --send` | Telegram | one alert run on stored data (no VECO fetch) |
| `bot` | Telegram | the subscription bot, until Ctrl+C |
| `map [--host] [--port]` | none (listens only) | the outage map at http://127.0.0.1:8765; read-only |
| `serve` | VECO + Telegram | check + alerts at 06:00 and 20:00 Manila, catch-up on start, bot and map threads (`--no-map`, `--map-host`, `--map-port`) |

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

## The map

`serve` also serves the map at **http://127.0.0.1:8765** (or run `python -m outages map` on its
own). Pick a day and an hour, or press ▶ to step through them, and see which barangays **may** be
without power then. Click a barangay for the time, purpose, streets and VECO's post. Times are
Manila time whatever your computer's zone.

- **"May be affected":** VECO writes "portion of" on nearly every line, so a whole barangay is
  shaded; the streets in the popup say which part.
- **Never hidden:** an outage the map can't place (VECO named the wrong town, a typo, places but
  no barangay, or areas not processed yet) is listed under **Not on the map** with the reason.
  Cancelled outages are greyed (toggle "Show cancelled"); outages VECO removed are counted.
- **Honest about age:** "Data as of …" is the last check; a warning shows when it is more than
  15 h old (a missed run), and outages from a post we couldn't re-read carry a caveat.
- **Other weeks:** `http://127.0.0.1:8765/?from=2026-10-01&days=7` (up to 14 days).
- **Read-only:** the map opens the database read-only and never contacts VECO or Telegram. If it
  can't start (port taken, missing boundaries), `serve` logs it, tells the admin chat once a day,
  and keeps running the alerts.
- **Your LAN:** `--host 0.0.0.0` (or `serve --map-host 0.0.0.0`) makes the map reachable from
  other devices. There is no login, so only do this on a network you trust; it logs a warning.

Attribution: map tiles © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors
(fetched by your browser; fine for personal use, see the OSM tile policy before anything public);
barangay boundaries from [faeldon/philippines-json-maps](https://github.com/faeldon/philippines-json-maps)
v1.1.1 (MIT), PSGC 3Q 2026, in `src/outages/areas/boundaries.geojson` (rebuilt by `gazetteer`);
[Leaflet](https://leafletjs.com) 1.9.4 (BSD-2-Clause), vendored in `src/outages/map/static/vendor/`.
