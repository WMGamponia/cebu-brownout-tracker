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
| `map [--host] [--port]` | none (listens only) | the outage map at http://127.0.0.1:8765 and `/history`; read-only |
| `stats [--from] [--to]` | none | history stats for a date range (default: the last 365 days); read-only |
| `streets fetch [--yes] [--towns]` | **Overpass** | download the OpenStreetMap street extract, one request per town; without `--yes` it only prints the requests |
| `streets match [--rebuild] [--dry-run]` | none | match VECO's street names to the extract (`check` and `reparse` also do it once an extract exists) |
| `streets --review` | none | street names not found, with close OSM names to consider as aliases; read-only |
| `eval sample\|run [--suite areas\|streets]` | none | the labelled evals (`--suite` defaults to `areas`) |
| `serve` | VECO + Telegram | check + alerts at 06:00 and 20:00 Manila, catch-up on start, bot and map threads (`--no-map`, `--map-host`, `--map-port`) |

## Telegram alerts

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token it gives you.
2. Create `.env` in the project root (git ignores it; never commit or paste it):
   ```
   TELEGRAM_BOT_TOKEN=<token from BotFather>
   TELEGRAM_ALLOWED_CHAT_IDS=
   TELEGRAM_ADMIN_CHAT_ID=
   ```
3. Run `python -m outages bot` and send your bot `/start`. Without an admin chat it answers
   "Not taking new requests right now" and logs `bot: join request from chat <id> refused`.
4. Put that ID in both keys (`TELEGRAM_ALLOWED_CHAT_IDS` takes several, comma-separated), restart
   the bot, send `/start` and `/add <your barangay>` (e.g. `/add Lahug`; a name in several towns
   gives buttons). `/list` and `/remove` manage subscriptions.
5. `python -m outages alerts --dry-run` shows what would be sent; `serve` sends it on schedule.

### Letting other people join

Anyone can find the bot and send `/start`. They become **pending**: they get a welcome saying the
bot runs on one person's computer (alerts can be late or missed) and what it stores, and the
admin chat gets `Join request: <first name> (@username, chat <id>)` with **Approve** / **Deny**
buttons. Approved people use `/add`, `/list`, `/remove` like you; denied chats are ignored from
then on. Chats in `TELEGRAM_ALLOWED_CHAT_IDS` never need approval.

- **Admin chat only:** `/pending` (requests with buttons), `/users` (first name, status, number
  of barangays, chat ID; never the barangays themselves), `/revoke <chat id>` (drops their
  subscriptions and alert history, clears the name, blocks the chat).
- **Everyone:** `/privacy` says what is stored (chat ID, first name, barangays, which alerts were
  sent) and why; `/forget` deletes all of it at once. A declined chat's ID is kept so the bot
  stays silent.
- **Limits:** private chats only (a group gets one refusal); at most 20 pending requests and 10
  barangays per person; a chat sending more than 20 messages a minute is ignored for 10
  minutes. Alerts go out one at a time, at least 50 ms apart. Logs carry chat IDs, never message
  text.

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
- **Other weeks:** the **Week of** picker (◀ ▶, a date, **Today**) shows any 7 days, with a
  "History: …" banner instead of the old-data warning; or `?from=2026-10-01&days=7` (up to 14).
- **History:** `/history` (the **History** link) shows a heat map of scheduled outage hours per
  barangay for a date range (default: the last 12 months), the 10 most-affected barangays,
  durations, towns, months, and what was counted or left out with the reason. These are VECO's
  *scheduled* interruptions, not measured outages. A barangay's hours count overlapping outages
  once; a barangay name found in two towns counts for both, and says so. `python -m outages
  stats` prints the same numbers.
- **Read-only:** the map opens the database read-only and never contacts VECO or Telegram. If it
  can't start (port taken, missing boundaries), `serve` logs it, tells the admin chat once a day,
  and keeps running the alerts.
- **Your LAN:** `--host 0.0.0.0` (or `serve --map-host 0.0.0.0`) makes the map reachable from
  other devices. There is no login, so only do this on a network you trust; it logs a warning.

## Streets

VECO names streets ("along portions of Gorordo Ave."). The map can draw them as lines inside the
listed barangays, from a local OpenStreetMap extract, but **only if matching is measured to be
precise**: a wrong line is worse than none. A frozen test set of 50 hand-labelled street
mentions decides (`evals/streets/`): drawing turns on only with precision ≥ 0.95 over ≥ 20 drawn
rows. The first run (2026-11-12) drew 13 rows at 0.923, so **drawing is off** (`STREETS_DRAW` in
`config.py`) and the popups list the streets in words, as before.

1. `python -m outages streets fetch` prints the 8 Overpass requests (one per VECO town, 10 s
   apart); add `--yes` to make them. Files go to `data/osm/` with a `manifest.json`. A town that
   fails keeps its old file; `--towns "Cebu City,Minglanilla"` refetches only those.
2. `python -m outages streets match --dry-run` counts; without `--dry-run` it writes
   `street_matches` (back up `data/outages.db` first). After that, `check` and `reparse` keep it
   current.
3. `python -m outages streets --review` lists names not found with close OSM names; an alias you
   accept goes in `src/outages/areas/street_aliases.csv` (`town_psgc,veco_key,osm_name`), then
   `streets match --rebuild`.

The extract and anything derived from it are OpenStreetMap data under the
[ODbL](https://opendatacommons.org/licenses/odbl/): they stay in `data/` (never committed), and a
map that draws lines says "Streets © OpenStreetMap contributors (ODbL)".

Attribution: map tiles © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors
(fetched by your browser; fine for personal use, see the OSM tile policy before anything public);
barangay boundaries from [faeldon/philippines-json-maps](https://github.com/faeldon/philippines-json-maps)
v1.1.1 (MIT), PSGC 3Q 2026, in `src/outages/areas/boundaries.geojson` (rebuilt by `gazetteer`);
[Leaflet](https://leafletjs.com) 1.9.4 (BSD-2-Clause), vendored in `src/outages/map/static/vendor/`.
