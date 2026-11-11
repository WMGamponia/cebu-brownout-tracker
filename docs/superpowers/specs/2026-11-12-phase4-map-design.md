# Phase 4 Map — Design

Status: approved in brainstorming 2026-11-12, section by section. Base: `main` at `9373e1c` (Phase 3 merged, PR #3). Branch: `phase4-map`.

## 1. Intent

A local web map of upcoming VECO outages: pick a day and an hour (or press play) and see which barangays **may** be without power, with the time, purpose, streets and a link to VECO's post. It is honest about what it can't place and about stale data, it never contacts visayanelectric.com, and it is a portfolio piece: small, tested, documented.

Success:
- `python -m outages map` (and `serve`) shows the outages from today onward from the real DB at `http://127.0.0.1:8765`, opening the DB read-only.
- No outage silently vanishes: an outage the map can't place appears in a "Not on the map" list.
- Times are Manila time whatever the browser's zone; overnight outages show on both days.
- CI checks the endpoint, a golden GeoJSON and the JS time logic, with no tile, VECO or Telegram request.

## 2. Decisions (owner, 2026-11-12)

| Topic | Decision |
|---|---|
| Server | **FastAPI + uvicorn** (new dependencies, approved). Plain `uvicorn`, not `[standard]`. TestClient runs on our existing httpx. |
| Leaflet | **Vendored 1.9.4** (`leaflet.js`, `leaflet.css`, images, LICENSE) in the package's static folder. The page loads nothing from outside except OSM tiles. |
| JS tests | **`node --test`** on a pure time module, no npm packages. Node is a CI/dev tool, not a runtime dependency. |
| Process | **Both:** `serve` starts the map in a thread (`--no-map` to turn it off); `python -m outages map` runs it alone. A map that fails to start never stops alert runs. |
| Access | **127.0.0.1 by default**; `--host 0.0.0.0` (or `serve --map-host`) for the LAN is a deliberate opt-in. No auth until Phase 6. |
| Unplaced rows | `other_town`, `unmatched` and not-yet-parsed areas are **never shaded**; their outages appear in a "Not on the map" side list with VECO's text and the reason. Same rule as alerts: a row naming the wrong town is not presented as a place. |
| Shading | **One "may be affected" style.** "Portion of" is on 3,022 of 3,025 lines, so the ROADMAP's soft-vs-full split would never carry information. The legend says "parts of these barangays may be affected"; streets are in the popup. |
| Statuses | `scheduled` and `additional` shaded; `cancelled` greyed, shown by default, with a toggle; `removed` counted only ("N outages no longer in VECO's post"). Removed outages have no `areas_hash`, so no stored barangays. |
| Names | Display name (PSGC name with `display_names.csv`, e.g. Santo Niño not Central), plus "VECO wrote: …" in the popup when VECO's spelling differs. |
| Shape | **One self-contained FeatureCollection**: one feature per barangay (VECO's 232, plus Lapu-Lapu and Cordova marked not covered), with top-level `outages` and `meta` members. Also the Phase 6 static snapshot's format. |
| Geometry | A **committed derived `areas/boundaries.geojson`**, written by `gazetteer` from the pinned zip (as `barangays.csv` is). CI needs no download. |
| Window | From today onward, by time: today 00:00 Manila to the date of the last stored end, capped at 7 days; `?from=YYYY-MM-DD&days=N` (1–14) for other ranges (Phase 5 history reuses it). |

## 3. Findings that shaped the design

From `data/outages.db` and `data/gazetteer/geojson-medres.zip` (read-only, 2026-11-11):
- **Geometry is small and coarse:** the 8 towns' 232 barangay polygons are 149 KB with ~2,000 vertices (~9 per barangay). No simplification or build pipeline needed; neighbouring shapes won't line up exactly, which suits a "may affect" map. Lapu-Lapu (`731100000`) and Cordova (`702223000`) have barangay files in the same zip.
- **"Portion of" is on 3,022 of 3,025 outage lines.**
- **Removed outages (17) have no `areas_hash`**: the areas step only hashes non-removed outages.
- **Posts overlap:** Nov 3–8 ends Nov 8 06:00 and Nov 8–14 starts Nov 8. Data reaches about a week ahead (latest stored start: Nov 14). "The week" is defined by time, not by post.
- **Times:** 237 outages cross midnight; starts are on the hour except 34 at :30, 4 at :50, 2 at :45. Durations are 1–16 h, mostly 8 h.
- **Barangays per outage:** mean 1.8, max 38; up to 44 outages per post.
- **Run gap:** runs at 06:00 and 20:00 leave a 14 h gap, so a "data is old" warning must use more than 14 h (15 h), not the 12 h catch-up threshold.

## 4. Components

```
src/outages/
  areas/boundaries.geojson   # committed; derived by `gazetteer`
  gazetteer_build.py         # + build_boundaries()
  map/
    __init__.py
    window.py      # pure: query → Window(from_date, to_date); default and caps
    build.py       # (conn, gazetteer, boundaries, window, now) → the document (dict)
    app.py         # create_app(db_path, boundaries, gazetteer, clock) → FastAPI
    server.py      # run() for `map`; start_thread() for `serve`
    static/
      index.html
      map.css
      js/timeline.js    # pure ES module: Manila time, active hours, day labels
      js/map.js         # Leaflet + DOM
      vendor/leaflet-1.9.4/{leaflet.js, leaflet.css, images/*, LICENSE}
tests/
  test_map_window.py
  test_map_build.py           # golden: tests/golden/map/*.geojson
  test_map_app.py             # TestClient
  test_boundaries.py
  js/timeline.test.mjs
```

`pyproject.toml`: `fastapi` and `uvicorn` in `dependencies` (lower bounds at the versions installed in Task 1); package data adds `areas/*.geojson` and `map/static/**/*`.

### 4.1 `boundaries.geojson`
`gazetteer_build.build_boundaries(zip_path, towns, meco_towns, out)` reads each town's `bgysubmuns-municity-<psgc>.0.01.json` and writes one FeatureCollection:
- features sorted by (covered desc, town, name); `id` and `properties.psgc` = `adm4_psgc` as a string; `properties`: `psgc`, `name` (the release's `adm4_en`), `town`, `town_psgc`, `covered` (true for `VECO_TOWNS`, false for `MECO_TOWNS = {"Lapu-Lapu City": "731100000", "Cordova": "702223000"}` in `config.py`);
- coordinates rounded to 6 decimals; no other properties;
- a top-level `attribution`: `Boundaries: faeldon/philippines-json-maps v1.1.1 (MIT), PSGC 3Q 2026`.

`python -m outages gazetteer` writes it next to `barangays.csv` (from the local verified zip; downloads only if the zip is missing, as today). Display names are not baked in: `build.py` applies the gazetteer's display names at request time, so `display_names.csv` stays the single source.

### 4.2 `window.py`
`Window(from_date, to_date)`, inclusive Manila dates; it covers `[from 00:00+08:00, to+1 00:00+08:00)`.
- Default (no query): `from` = today (Manila, from the injected `now`); `to` = the Manila date of the latest `end_at` among non-removed outages with `end_at > from 00:00`, capped at `from + 6`; no such outage → `to = from`. An `end_at` of exactly 00:00 counts toward the previous day.
- `?from=YYYY-MM-DD&days=N`: `to = from + N − 1`. `days` defaults to 7; must be 1–14. `from` must be a valid date. Otherwise 422 with the reason.
- An outage is **in the window** when `start_at < window end` and `end_at > window start`.

### 4.3 `build.py` — the document
`build(conn, gazetteer, boundaries, window, now) -> dict`. Read-only SQL, one query per concern (outages in window; their barangay rows; their entities; removed count; stale posts; `meta.last_check_at`).

```jsonc
{
  "type": "FeatureCollection",
  "features": [{                      // every boundary feature, always, in boundaries order
    "type": "Feature", "id": "730600022", "geometry": { … },
    "properties": {
      "psgc": "730600022", "name": "Santo Niño", "town": "Cebu City", "covered": true,
      "outage_ids": [4012, 4019]       // shaded outages in the window; [] → outline only
    }}],
  "outages": {                         // keyed by outage id (string), every non-removed outage in the window
    "4012": {
      "start": "2026-11-12T22:00:00+08:00", "end": "2026-11-13T05:00:00+08:00",
      "status": "scheduled",           // scheduled | additional | cancelled
      "purpose": "…", "areas_raw": "…",
      "post": {"url": "…", "title": "…"},
      "stale": false,
      "barangays": [{"psgc": "…", "name": "Tayud", "town": "Liloan",
                     "veco_name": "Tayud", "ambiguous_with": ["Consolacion"]}],
      "entities": {"street": [], "sitio_purok": [], "subdivision": [],
                   "establishment": [], "other": []},
      "unplaced": [{"name": "Poblacion", "towns": ["Minglanilla"], "why": "other_town"}]
    }},
  "meta": {
    "generated_at": "…+08:00", "last_check_at": "…+08:00",   // null if never checked
    "window": {"from": "2026-11-12", "to": "2026-11-14"},
    "days": ["2026-11-12", "2026-11-13", "2026-11-14"],     // every date from..to
    "removed_count": 0,                                       // removed outages in the window
    "stale_posts": [{"url": "…", "title": "…"}],
    "attribution": "…"                                        // from boundaries.geojson
  }
}
```

Rules:
- **Shaded rows:** `area_barangays` with `match ∈ (exact, alias, ambiguous)` for the outage's `areas_hash`. The outage's id goes on each such barangay's feature (deduplicated, sorted). `name` is the gazetteer's display name; `veco_name` is `name_raw`; `ambiguous_with` lists the other towns (by name) of the same `name_raw` rows in that parse, `[]` unless `ambiguous`.
- **Unplaced rows:** `other_town` → `{name: name_raw, towns: <VECO's listed towns>, why: "other_town"}`; `unmatched` → `why: "unmatched"`; an outage whose `areas_hash` is NULL or has no `area_parses` row → one item `{name: null, towns: [], why: "not_parsed"}`. VECO's listed towns come from `area_parses.towns_json` (as Phase 3's admin notes do), not from the row's `town_psgc`, which is the town of the matched code (checked: `Poblacion` rows carry Liloan's and Talisay's town codes). `towns_json` can be `[]` (barangay-first lines); the page then omits the town. Rows with the same (`name_raw`, `match`) give one unplaced item.
- **Entities:** `area_entities` grouped by kind, in `seq` order, all of them (the page truncates).
- **Stale:** the outage's post has `parse_status = 'failed'` (the same signal Phase 3 alerts use). `stale_posts` lists those posts that have outages in the window.
- **Removed:** counted in `removed_count`, not in `outages`.
- **Time strings:** stored ISO strings as stored (`+08:00`); `generated_at` and `last_check_at` converted to `+08:00`.
- **Determinism:** `now` injected; `outages` keys sorted by (start, id) on insertion; barangays by psgc; JSON written with `ensure_ascii=False`.

### 4.4 `app.py`
`create_app(db_path, boundaries, gazetteer, clock=utcnow)`; GET only.

| Route | Returns |
|---|---|
| `/` | `static/index.html` |
| `/static/…` | static files (`StaticFiles`) |
| `/outages.geojson?from=&days=` | the document, `application/geo+json; charset=utf-8`, `Cache-Control: no-store` |
| `/healthz` | `{"ok": true, "schema": 3, "last_check_at": …}` |

- Each request opens `db.connect_ro(db_path)` and closes it. The map never writes or migrates.
- The page fetches `outages.geojson` by **relative** URL, so the Phase 6 snapshot is the page plus that file.
- No CORS headers, no docs UI in production (`docs_url=None`, `redoc_url=None`, `openapi_url=None`), so the surface is exactly the table above.

### 4.5 `server.py`
- `run(app, host, port)`: `uvicorn.Server(Config(app, host, port, log_config=None))`, using the project's logging.
- `start_thread(app, host, port) -> Thread`: the same server on a daemon thread. Signal handlers stay with the main thread (uvicorn only installs them on the main thread). Startup failure (port in use, bad boundaries) is caught (uvicorn calls `sys.exit(1)` when it can't bind, so the wrapper catches `SystemExit` too and checks `server.started`), logged at ERROR, sent once to the admin chat when Telegram is configured, and the thread ends. The scheduler is unaffected.
- Defaults in `config.py`: `MAP_HOST = "127.0.0.1"`, `MAP_PORT = 8765`.

### 4.6 The page
**Layout:** a full-screen map; a top bar; a side panel (a drawer below 768 px).
- **Top bar:** "Data as of Thu 12 Nov, 20:00". A warning when `last_check_at` is more than **15 h** old (a run was missed) or null. The stale caveat, linking each stale post: "VECO updated this post and we couldn't read the update; this is from the earlier version."
- **Controls:** day tabs from `meta.days` ("Today", "Fri 13", …); an hour slider 0–23 labelled "22:00–23:00"; ▶/❚❚ play, one hour per second across all days, stopping at the last hour; "Show cancelled", on by default.
- **Default:** today's tab and the current Manila hour (if today isn't in `meta.days`, the first day at 00:00).

**Time logic (`timeline.js`, pure, no DOM):**
- Parse the `+08:00` ISO strings to epoch ms; Manila wall time = epoch + 8 h read with UTC getters. Never `Date#getHours`, never the browser's zone. The Philippines has no DST, so a fixed offset is exact.
- **Active in hour h of day d:** `start < d (h+1):00` and `end > d h:00` (Manila). A 22:00–05:00 outage is active 22–23 on day 1 and 0–4 on day 2; a :30 or :45 edge makes that whole hour active.
- **On day d:** overlaps `[d 00:00, d+1 00:00)`.
- Exports (names fixed in the plan): parse, Manila parts, active-in-hour, on-day, day label, range label (dates shown when it crosses midnight), default selection.

**Barangay styles for the selected day and hour:**

| Barangay state | Style |
|---|---|
| An active scheduled/additional outage | soft orange fill + solid outline |
| Only active cancelled outages (shown) | light grey fill, dashed outline |
| An outage that day, not in this hour | faint orange outline, no fill |
| Nothing that day | hairline grey outline |
| `covered: false` | grey fill; tooltip "MECO area, not covered" |

Legend: "Shaded: parts of these barangays may be affected."

**Popup (click a barangay):** name and town; every outage there on the selected day, active ones first and highlighted. Each: the time range (dates when it crosses midnight), an Additional/Cancelled badge, purpose, up to 8 streets/sitios then "+N more" (expands), "VECO wrote: …" when `veco_name` differs from the name, "VECO lists Tayud under Consolacion and Liloan." when ambiguous, the stale caveat when `stale`, and a "VECO post" link.

**Side panel:** "Not on the map (N)" for the selected day: time, VECO's text, the reason in words (`other_town`: "VECO listed Poblacion under Minglanilla; no Minglanilla barangay has that name", or "VECO's town for Poblacion has no barangay of that name" when `towns` is empty; `unmatched`: "We couldn't match Lipata to a barangay"; `not_parsed`: "Areas not processed yet"), and the post link. Then "N outages no longer in VECO's post", the legend, and attribution.

**Safety and behaviour:** VECO text goes in with `textContent`; links with `setAttribute('href', …)` after checking the URL starts with `https://www.visayanelectric.com/`. No `innerHTML` with data. A failed fetch shows a banner with Retry. The data is refetched when the tab becomes visible after more than 30 minutes. Tiles: `https://tile.openstreetmap.org/{z}/{x}/{y}.png`, attribution "© OpenStreetMap contributors", `maxZoom: 19`, no prefetch or bulk loading. The initial view fits the covered barangays.

## 5. CLI

`_run` keeps explicit dispatch; `map` is an offline command.

| Command | Network | Does |
|---|---|---|
| `map [--host 127.0.0.1] [--port 8765]` | none (listens only) | Serves the map. Builds no fetcher and no Telegram client. Exits 1 with a message if the boundaries can't load or the port is taken. |
| `serve [--no-map] [--map-host …] [--map-port …]` | VECO + Telegram, as before | Also starts the map thread unless `--no-map`. |
| `gazetteer` | GitHub release, only if the zip is missing | Also writes `areas/boundaries.geojson`. |

A non-loopback host logs a WARNING: "map is reachable from your network; there is no login".

## 6. Error handling

| Situation | Behaviour |
|---|---|
| DB missing or schema < 3 | `/outages.geojson` → 503 `{"error": "database not ready: run python -m outages areas"}`; the page shows the banner. `/healthz` → 503. |
| Bad `from` / `days` | 422 with the reason. |
| Outage with unparsed areas | `why: "not_parsed"` in the side list; not an error. |
| `boundaries.geojson` missing or invalid | `create_app` raises; `map` exits 1; `serve` logs, notes the admin chat once, keeps alerts running. |
| Port in use | As above. |
| DB busy during a run's write | The read waits on `busy_timeout`. |
| Unexpected exception in a request | 500, traceback in the log, generic body. |
| Tiles unreachable (offline) | Polygons still draw on a blank background; Leaflet's tile errors are ignored. |

## 7. Testing

No VECO, Telegram or tile request in CI; the suite-wide network block stays.
- **`test_boundaries`:** the committed file vs `barangays.csv`: every VECO code exactly once with `covered: true`; every other feature is in a MECO town with `covered: false`; attribution present; coordinates have ≤ 6 decimals. `build_boundaries` on a tiny synthetic zip (golden).
- **`test_map_window`:** default window (from today to the last end), cap at 7 days, empty DB → today only, an `end_at` at 00:00, explicit `from`/`days`, 422 cases, overlap at both edges.
- **`test_map_build`:** a fixture DB with scheduled, additional, cancelled, removed, a stale post, ambiguous `Tayud`, `other_town` `Poblacion` under Minglanilla, `unmatched` `Lipata`-style, not parsed, an overnight outage, a :30 edge, Santo Niño (display name vs `name_raw`), an outage outside the window. Compared with `tests/golden/map/default.geojson` at an injected `now`; a second golden for an explicit `from`/`days`.
- **`test_map_app` (TestClient):** 200 with the content type and `no-store`; `/` and a static file; 503 and 422; `/healthz`; no `/docs`; the DB file's hash is unchanged after requests; no fetcher or Telegram client is built.
- **`test_cli`:** `map` dispatched explicitly and offline (the fetcher factory raises if called); `serve --no-map` starts no map thread; a map thread that fails to bind leaves the scheduler running; a non-loopback host logs the warning.
- **`tests/js/timeline.test.mjs`:** active-hour edges (start at h, end at h, :30 edges), overnight split across days, on-day, default selection (now inside and outside `meta.days`), labels; run under `TZ=America/New_York` and `TZ=Asia/Tokyo` to show the browser's zone is ignored.
- **CI:** a `js` job with `actions/setup-node` (Node 22) running `node --test tests/js/` under both TZs. Python jobs unchanged apart from the new dependencies.
- **By hand, once:** the real DB in a browser at desktop and phone widths; screenshots in the PR. There is no browser in CI.

## 8. Build order (for the plan)

1. Dependencies (FastAPI, uvicorn), `config` (`MAP_HOST`, `MAP_PORT`, `MECO_TOWNS`), package data.
2. `build_boundaries` + `gazetteer` writes `boundaries.geojson` from the local zip; commit the file; `test_boundaries`.
3. `map/window.py` (TDD).
4. `map/build.py` + fixture DB + golden files.
5. `map/app.py` (TestClient).
6. `map/server.py`, `map` command, `serve` thread and flags; CLI tests.
7. `timeline.js` + `node --test`; CI `js` job.
8. Vendor Leaflet; `index.html`, `map.css`, `map.js`.
9. Real-DB check by hand (read-only, DB hash unchanged), screenshots; owner review.
10. README (running the map, LAN opt-in, attribution), ROADMAP, Phase 5 prompt.

## 9. Definition of done
- All of §7 passes on Ubuntu and Windows (Python) and in the Node job; ruff clean.
- `python -m outages map` on the real DB shows the outages from today on; the DB hash is unchanged afterwards.
- `serve` serves the map on 127.0.0.1:8765 next to the scheduler and bot.
- Checked by hand and shown to the owner: an overnight outage appears on both days; cancelled toggles; the "Not on the map" list agrees with `report` and the stored rows; Santo Niño shows as Santo Niño.

## 10. Gates (ask the owner first)
- Installing the new dependencies (approved in brainstorming; done in Task 1).
- Running the map on `0.0.0.0` / the LAN.
- Any `gazetteer` download (not needed: the zip is local and verified).
- Push, PR, merge.

## 11. Out of scope
Street geometry (Phase 5); a history UI and stats (Phase 5; `from`/`days` is the hook); auth and the static snapshot (Phase 6); live updates or push; tile caching or a self-hosted tile server; a list or search of barangays beyond the map itself.

## 12. Risks and accepted limits
- **The map overstates by design:** a whole barangay is shaded for "portion of"; the popup's streets narrow it in words only until Phase 5.
- **Medium-resolution polygons are coarse;** edges between barangays are approximate.
- **OSM tile policy:** fine for one person's local use. A public Phase 6 demo must re-check the policy (or use another tile provider) before it goes live.
- **The map is only as fresh as the last run** (up to 14 h between runs); the top bar says so, and warns past 15 h.
- **Coupling with `serve`:** the map thread shares the process. A startup failure is contained (§4.5); a hung request can't block the scheduler (separate threads), but a memory leak in the web thread would affect `serve`. Accepted for one user.
- **Node is a dev/CI tool:** running the JS tests locally needs Node; Python tests don't.
