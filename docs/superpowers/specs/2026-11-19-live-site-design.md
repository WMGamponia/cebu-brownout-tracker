# Live site: the PC publishes the snapshot to GitHub Pages after each check

Date: 2026-11-19 · Branch: `feat/live-site` · Status: draft for the owner's approval

## 1. Intent

The demo at `wmgamponia.github.io/cebu-brownout-tracker/` is a snapshot refreshed by hand, last
on 16 Nov. The owner wants it to stay current by itself, for two audiences (owner, 2026-11-18):

- **Friends and family** who may look before a brownout. For them, out-of-date data must say so
  plainly, and the owner must hear when publishing breaks.
- **Employers** checking the project. For them the site should look alive and stay honest about
  what it is.

Success: after every successful 06:00 / 20:00 check on `main`, the site shows that check's data
within minutes, with the real clock, and nobody has to do anything.

## 2. Decisions made (owner)

- **GitHub Pages, not Cloudflare Pages** (2026-11-19). This replaces the 2026-11-15 roadmap plan
  (`wrangler pages deploy`). Kept: the URL in the README and portfolio, no new account, token or
  Node tooling. Measured, not assumed: the November usage report shows `pages-build-deployment`
  runs on this public repo at $0 and **no Actions storage** for it; the storage alert was WheelPad's
  artifacts. The one Cloudflare advantage left, control of cache headers, is handled by versioned
  paths (§5.4).
- **Before Discord** (2026-11-18): this phase comes next; Discord DMs follow.
- **`serve` publishes in-process**, after the run, not a separate scheduled task: the snapshot
  then always reads a finished check (closes Phase 6 deferred minor (1)).
- **Only from `main`.** The owner rejected checking out `main` and back, and a separate `main`
  worktree to build from. On any other branch, the run skips publishing.
- **`gh-pages` holds one commit**, replaced on each publish (force-push with lease).
- **No third run** (2026-11-19). In the first 8 live days none of VECO's 5 changes would have
  reached anyone sooner through a midday check. Revisit after a month of on-time 06:00 runs using
  `outage_changes.detected_at`: add one if same-day, pre-start changes after 06:00 come more than
  about once a month.
- **Late mornings are a PC problem, not a code problem.** The owner shuts the PC down at night, so
  the existing 05:58 wake task can't fire and `serve` waits for a login (06:00 ran at 07:47, 07:29
  and 09:34 on 17–19 Nov). The owner is trying hibernate (sleep as fallback). No code in this phase.

## 3. Approaches considered

| | Trade-off | Verdict |
|---|---|---|
| **In `serve`, after the check** | One process, ordered after the check, holds `serve.lock`; reuses git credentials | **Picked** |
| Separate Windows task at 06:30 / 20:30 | Leaves `serve` untouched, but a guessed gap: a slow or caught-up check could still be writing | Dropped |
| GitHub Actions builds and deploys | The DB lives on the PC | Dropped |
| Cloudflare Pages via `wrangler` | Own cache headers, atomic deploys; new URL, account, token, Node | Owner chose GitHub Pages |
| A hosted DB the site queries | Rewrites tested Python into SQL/JS, public key in the page, quotas strangers can burn | Dropped: the site needs ~60 precomputed files, not queries |
| Check out `main`, run, check out back | Swaps files under a running process (mixed code), breaks uncommitted work | Owner rejected |

## 4. Goals, non-goals, anti-goals

**Goals**
- G1. Each successful check run by `serve` on `main` (cron, watchdog catch-up, or catch-up at
  start) publishes a fresh snapshot.
- G2. While the data is fresh, both pages use the real clock; when it is old, they say so.
- G3. A visitor never sees new and old files mixed, whatever the 10-minute Pages cache holds.
- G4. A failed or skipped publish never delays or blocks alerts, and the admin chat hears once.
- G5. Off by default; nothing in tests or a fresh clone can push.

**Non-goals**
- Publishing on demand, from the bot, or more often than the checks.
- Watching whether GitHub's Pages build succeeded after the push.
- Waking or powering on the PC (§2).

**Anti-goals** (a failure even if it "works")
- An unmerged branch's pages or data reaching the public site.
- The page showing old data as if it were current.
- A chat ID, the bot token or subscriber data in a pushed file (the existing leak scan stays the
  gate).
- The admin chat getting a message every run while something stays broken.

## 5. Design

### 5.1 When it runs

In `_run_once`, after the alert run and **before** the street suggestions (Qwen can take minutes):

```
check → alerts → publish → street suggestions
```

Only when **this run checked and the check succeeded** (`do_check` and `check_ok`). Alert-only
runs (every `serve` start with data under 12 h old; 10 of them on 15 Nov) and failed checks
publish nothing: they have no new data, and the page's freshness keys on `last_check_at`, so
nothing is lost.

### 5.2 Gate

Publishing happens only when all hold; otherwise the run logs why and moves on.

1. **Enabled:** `PUBLISH_SITE=1` in `.env` (default off). The target is `PUBLISH_DIR`, default
   `<repo root>/site`, and must be a git worktree on branch `gh-pages`.
2. **On `main`, at start and now:** `serve` records the checkout's branch when it starts; at
   publish time the branch is read again. Both must be `main`. (Python was loaded at start; the
   page files are read from disk at publish time. Checking both keeps either from another branch.)
3. **No tracked changes** under `src/outages/` (`git status --porcelain -- src/outages`).
   Untracked files are ignored.
4. **Fresh data:** the existing `snapshot` rule (`last_check_at` ≤ `STALE_CHECK_H` = 15 h) stays
   as a backstop.

Skips for 2 and 3 send one admin note per Manila day and reason
(`publish:skip:<reason>:<YYYY-MM-DD>`, through `admin_notes_sent`), e.g.
"Site not published: serve is on feat/x, not main." A skip for 1 is silent (off is normal).

### 5.3 Build and push

1. **Build** with the code `snapshot` uses today, refactored from `__main__._snapshot` into a
   function that returns a result instead of printing and exiting (the CLI keeps its output and
   exit codes). Same guards: read-only connection with the authorizer guard, leak scan, folder
   check (updated for the layout in §5.4).
2. **Write** into `PUBLISH_DIR`.
3. **Commit as one parentless commit:** `git add -A`, a commit with no parent from the index tree,
   message `snapshot: <taken_at>`.
4. **Push** `git push --force-with-lease=gh-pages:<previous gh-pages sha> origin <new sha>:gh-pages`,
   then move the local `gh-pages` branch to it. Git runs with `GIT_TERMINAL_PROMPT=0` and a 120 s
   timeout, so a credential prompt or a hung network fails instead of blocking the scheduler
   thread.
5. **On failure** (build error, leak, git error, timeout): the last good site stays up; log it
   with git's stderr; one admin note per Manila day (`publish:fail:<YYYY-MM-DD>`) with the error's
   first line. Never raises into `_run_once`.

All git calls go through one small runner that tests replace; no test runs a real `git push`.

### 5.4 Versioned paths (no mixed files)

GitHub Pages sends `Cache-Control: max-age=600` on everything and it can't be changed, so for up
to 10 minutes after a publish a browser may reuse files from before it (the 15 Nov lesson: a cached
`index.html` with a new `map.js` broke the page). Instead of controlling headers, no file path is
ever reused for different content:

- **Data** goes under `data/<id>/` (`weeks/`, `stats/`, `boundaries.geojson`), `<id>` from
  `taken_at` (e.g. `20261119T200105`). `snapshot.json` stays at the root, is always fetched with
  `no-store` (as now), and names the folder: `"data": "data/<id>/"`. `source.js` prefixes every
  data URL with it.
- **Code** (`static/`) goes under `static/<hash>/`, `<hash>` a short hash of all static files'
  bytes, so it changes only when the code does. The two HTML pages are rewritten to point there
  (as `_page` already rewrites `data-mode` and the history link); module imports are relative and
  keep working.
- **Keep the previous version** of each (`data/` and `static/`) and prune older ones, so a page
  loaded from a cached `index.html` or a just-replaced `snapshot.json` still finds its files.
  This also closes Phase 6 deferred minor (2) (`static/` never pruned).
- `index.html`, `history.html`, `snapshot.json`, `LICENSES.md`, `.nojekyll` stay at the root.

### 5.5 The pages: real clock while fresh

`snapshot.json` gains `"fresh_for_h": 15` (from `STALE_CHECK_H`). In `source.js`:

- **Fresh** (`Date.now() - last_check_at ≤ fresh_for_h`): `now()` returns `Date.now()`. The map's
  "Now", the timeline and the history page's default range use the real clock.
- **Old** (beyond that, or `last_check_at` missing): `now()` stays pinned to `taken_at`, as today.
- Decided once at page load (my call: simple; a reload re-decides).

Banner and credits (`page.js`):

- **Fresh:** no banner. Credits: "Updated <last_check_at> (Manila) from VECO's advisories · code
  · licences".
- **Old:** the banner returns: "Last updated <last_check_at> (Manila) and may be out of date. For
  current schedules see VECO's advisories."

If the real clock passes the end of the stored window (the last outage end, at most 7 days), the
map shows no outages for that moment and must not error. A visitor's wrong clock isn't handled.

### 5.6 Docs

- README: "a snapshot of 12 Nov 2026, not live" becomes "updated after each check (06:00 and
  20:00 Manila) from a PC; when it's off, the page says the data is old". "Publishing the
  snapshot" keeps the by-hand steps for first setup and adds `PUBLISH_SITE` / `PUBLISH_DIR`.
- ROADMAP: replace "Live site on Cloudflare Pages (after Discord)" with this phase; log the
  decisions of 2026-11-18/19; mark Phase 6 deferred minors (1) and (2) fixed; update the Known
  issue "`serve` runs whatever branch the main checkout is on" (publishing refuses other
  branches); add the late-morning cause (PC shut down, not asleep).

## 6. Tests

- Gate: disabled, wrong branch at start, wrong branch now, tracked changes, old data → no build,
  no push; the right note key at most once per day; disabled sends no note.
- Trigger: alert-only run and failed check → no publish; successful check → publish.
- Push path with a fake git runner: commands in order, lease uses the previous sha, timeout and
  non-zero exit become one `publish:fail` note.
- `_run_once` order: publish after alerts, before suggestions; a raising publish doesn't stop the
  suggestions.
- Layout: data under `data/<id>/`, static under `static/<hash>/`, pages point at that hash, the
  previous version kept and older ones pruned, the same hash for unchanged static files;
  `check_out_dir` accepts the new layout and the old one (first publish over today's `site/`).
- CLI `snapshot` keeps its output and exit codes after the refactor (existing tests).
- `node --test`: fresh vs old `now()`, exactly at `fresh_for_h`, missing `last_check_at`, data
  URLs carry the `data` prefix; under both CI time zones.

## 7. Constraints

- Offline tests; nothing in CI touches GitHub, VECO or Telegram.
- No new runtime dependencies (git as a subprocess).
- Free: GitHub Pages on the public repo, ~60 builds a month (10 an hour soft limit).
- Live mode (`serve`'s own map at `127.0.0.1:8765`) is unchanged.

## 8. Left to the builder

Module and function names; how the branch is read; exact note wording; the `<id>` and `<hash>`
formats; where the start branch is stored.

## 9. Risks

- **PC shut down at night:** until hibernate or sleep is in place, mornings publish when the
  owner logs in; past ~11:00 the page shows the "may be out of date" banner. Honest, not broken.
- **A Pages build fails after a good push:** not detected (non-goal); the old site stays up.
- **First publish wipes `gh-pages` history** (5 hand commits). Intended.
- **The main checkout is on `feat/ui-refresh`**, three commits ahead of `main` (unmerged). Until
  those are merged or dropped and the checkout is on `main`, every check skips publishing and
  says so once a day.
