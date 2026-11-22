# Tray app: see whether `serve` ran and published, without opening anything

Date: 2026-11-22 · Branch: `feat/tray-app` · Status: draft for the owner's approval

## 1. Intent

The owner can't tell whether `serve` ran or published (owner, 2026-11-22). The trigger: the
check ran, but publishing was skipped because the checkout wasn't on `main`. The skip went to the
Telegram admin chat, which the owner rarely reads, so nobody noticed. At first the owner thought a
running game had blocked the run.

The main goal is visibility. A few controls come second.

Success: you can see from the tray icon whether `serve` is alive and whether the last run and
publish went fine. Anything that needs you (a failure, a skip, a crashed `serve`, a join request)
shows up as a Windows notification. Nothing has to be opened to find out.

## 2. Decisions made (owner, 2026-11-22)

- **Visibility first.** The icon is the status, and controls are one right-click away.
- **Kept:** run now (check, alerts and publish), approving or denying Telegram join requests,
  start at logon on or off.
- **Dropped:** editing the scheduled times (06:00 and 20:00 stay fixed), the list of apps that
  would block runs (the "blocked by a game" case was really the branch), and the barangay list
  (the map already has it).
- **Approach C: the tray starts and supervises `serve`** (see §4).
- **A right-click menu, not a window.**

## 3. Goals, non-goals, anti-goals

**Goals**
1. The icon shows one of four states: running fine, needs attention, broken, or not running
   (§5.3).
2. A Windows notification for every change that needs the owner (§5.4). Nothing for success.
3. Run now, approve or deny join requests, start at logon on or off, open the log, open the map,
   and Quit.
4. `serve` restarts by itself after a crash, up to a limit.

**Non-goals:** editing schedules, an app blocklist, a barangay list, a settings window, any change
to how alerts are chosen or rendered, and Discord.

**Anti-goals** (would count as failures even if everything technically works):
- A green icon while `serve` is dead or the last publish was skipped.
- Notifications on every successful run. They'd become noise the owner stops reading, which is
  how the Telegram notes got missed.
- The tray taking `serve` down with it. If the tray crashes, alerts must keep going.
- Admin actions that another program on the PC, or a web page, could trigger.

## 4. Approaches considered

| | How | Trade-off |
|---|---|---|
| A | Tray thread inside `serve` | Smallest. But when `serve` crashes the icon disappears too, so the failure that matters most can't be shown. |
| B | A separate tray process that only watches | Shows a dead `serve`, but can't recover it. Two processes to start at logon. |
| **C (picked)** | The tray is the logon task and runs `serve` as a hidden child process, restarting it when it crashes | Shows a dead `serve` and recovers it without the owner doing anything. The cost is managing a child process on Windows: hidden console, clean stop, restart limit. |

## 5. Design

### 5.1 Processes

- New command: `python -m outages tray`, started with `pythonw` from the logon task. On anything
  other than Windows it exits with an error. `pystray` and `Pillow` go in a new optional extra
  `[tray]`, imported only inside the tray module, so the Ubuntu CI job and the other commands
  don't need them.
- The tray starts `pythonw -m outages serve` as a child process with no console window (same
  flags as `publish.py`'s git calls). It always starts it with the map on loopback (never
  `--no-map`), because the admin routes live on the map app. `serve` writes to its own file log as
  it does now.
- **Admin token (my call).** Each `serve` start writes a new random token to `data/admin.token`
  (`data/` is gitignored, and the file is readable by the owner's Windows account only). The tray
  reads it from there. Any tray can then control any running `serve`. **Boundary:** the token keeps
  out web pages and other Windows users. It doesn't keep out programs running as the owner, which
  can already write the database directly.
- The tray itself doesn't take `serve.lock`. **Adopting a running `serve` (my call):** if `serve`
  exits because the lock is held (one started by hand, or left running after a tray crash), the
  tray adopts it. It reads the token file and controls it like its own child, and the menu says
  "serve started elsewhere". It can't restart an adopted `serve` when it dies, because it isn't
  the parent. So it watches the lock instead: once the lock is free, it starts its own `serve`.
  Quit stops the adopted `serve` too, so the menu item reads "Quit (stops serve)".
- If the tray crashes, the child keeps running, since Windows doesn't kill children with their
  parent. The logon task restarts the tray (the existing restart-on-failure setting), and the new
  tray adopts the orphaned `serve`.

### 5.2 Status stored by `serve`

The tray gets status by reading the database read-only, plus `GET /healthz`. `serve` saves
outcomes that currently only go to the log, as new `meta` keys written at the end of each run:

| key | value |
|---|---|
| `last_run_at` | UTC time the run finished |
| `last_run_kind` | `scheduled`, `catch_up`, `watchdog`, or `manual` |
| `last_check_ok` | `1` / `0` |
| `last_publish_status` | `published`, `skipped`, `failed`, or `off` (no publisher configured) |
| `last_publish_detail` | `Outcome.detail`, e.g. `abc1234 snapshot …` or `not on main` |
| `last_publish_at` | UTC time of that outcome |

`last_check_at` keeps its current meaning: the last *successful* check.

**Which runs write what (my call):**
- `last_run_*` and `last_check_ok` are written only by runs that do a check.
- The alerts-only run that `serve` does at start-up when no catch-up is needed (`do_check=False`)
  writes nothing. Otherwise every restart would hide the last real run's result.
- `last_publish_*` is written only when a publish is actually tried, or set to `off` when there's
  no publisher. So a failed check, which skips publishing, keeps the earlier publish outcome. The
  failed check already turns the icon red on its own.

### 5.3 Icon states

Worst one wins:

| State | When |
|---|---|
| **Red**: broken | `serve` hit the restart limit, or the last check failed, or the last publish was `failed` or `skipped`, or `serve` is alive but `/healthz` hasn't answered for 60 s (e.g. port 8765 in use) |
| **Grey**: not running | `serve` stopped by the owner, or starting up (no `/healthz` answer yet, under 60 s) |
| **Amber**: attention | a run time passed with no run for more than 10 min, or the data is older than 15 h (the site's threshold), or `serve` is restarting |

**A missed run (my call):** `scheduler.missed_slot` is computed against `last_run_at`, not
`last_check_at`. A failed check is a run that happened (red), not a missed one (amber). That
matches `serve`'s watchdog, which doesn't retry a slot whose check failed.

**`serve` alive but not answering (my call):** red, with the reason "serve is running but not
answering (map port in use?)", plus a notification. No automatic restart, because a restart
wouldn't free the port. "Restart serve" kills the process tree, since `/admin/stop` can't be
reached.
| **Green** | otherwise |

`off` publishing (no `PUBLISH_SITE`) is not red. The status line says "publishing off".

Hover text (tooltip): `Last run 20:00 OK · published abc1234 · next 06:00`.

The tray re-reads the status every 30 s and right after any action.

### 5.4 Notifications

Using `pystray`'s Windows notification. Each one is sent **once per event**.

**What counts as one event (my call):**
- An event is a **(reason, run)** pair. The run is `last_run_at`, or the time `serve` crashed or
  stopped answering. So a 20:00 failed check gets its own notification even when the icon is
  already red from a 06:00 skip, while the same unchanged problem isn't repeated every 30 s.
- Keys already notified are saved in `data/tray_state.json`, so restarting the tray doesn't
  repeat them.

Notifications are sent for:

- Each new red reason, e.g. "Publish skipped: not on main", "Check failed: VECO not reachable",
  "serve keeps crashing; restarts stopped", "serve is running but not answering".
- `serve` crashed and was restarted.
- A new join request, once per chat ID (name and "right-click to approve").
- A missed run (amber because of a missed slot).

No notification when a run succeeds or when the state goes back to green. The Telegram admin notes
keep working as before, as the backup when the owner is away from the PC.

### 5.5 Admin endpoints on `serve`

New routes on the map's existing FastAPI app, added only when it's bound to loopback (they stay
on the map app rather than their own listener; my call):

- `POST /admin/run`: starts a manual run through the same `_Runs` lock as the scheduled runs.
  If a run is already in progress it returns `409 already running`, and the tray notifies
  "A run is already in progress". A manual run is the same as a scheduled one: check, alert run,
  publish (with the existing `main`/clean gates), street suggestions. It doesn't use up a slot,
  so the next scheduled run still happens. It returns when the run starts, not when it ends.
- `GET /admin/pending`: pending join requests (`store.pending`).
- `POST /admin/decide` `{chat_id, approve}`: `store.decide`, then the same message to the person
  as the Telegram button sends (`APPROVED` / `DENIED`).
- `POST /admin/stop`: sets `serve`'s stop event. `serve` finishes any run in progress, then exits
  cleanly.

Access: the request must carry the token in a header and must have **no** `Origin` header. That
means only a program holding the token can call them, and no browser page can. On wrong or missing
token: 403.

### 5.6 Menu

```
● Running fine                           (state, not clickable)
Last run: Sun 22 Nov 20:00 · OK
Publish: published abc1234               (or: skipped: not on main)
Next run: Mon 23 Nov 06:00
───────────────
Run now
Join requests (2)  ▸  Juan — Approve / Deny
                      Maria — Approve / Deny
───────────────
Open log                                 (data/logs/outages.log in the default editor)
Open map                                 (http://127.0.0.1:8765/)
Start at logon  ✓
───────────────
Start serve / Restart serve              (whichever applies)
Quit
```

"Join requests" is greyed out when there are none. Run now and Join requests are greyed out while
`serve` isn't answering.

### 5.7 Supervision

- On an unexpected exit, restart after 10 s, then 60 s, then 5 min. A fourth crash within 30 min
  stops restarts: red, a notification, and "Start serve" in the menu.
- Exit because the lock is held → adopt the running `serve`, no restart (§5.1).
- **Quit** and **Restart serve**: `POST /admin/stop`, wait up to 10 min for a run in progress to
  finish (the menu says "Stopping after the current run…"), then kill the process tree as a last
  resort. Quit then closes the tray.
- Windows sign-out or shutdown: best effort; same stop, shorter wait.

### 5.8 Start at logon

`scripts/install-task.ps1` registers the logon task to run `pythonw -m outages tray` instead of
`serve`, with the same name and settings. The wake task doesn't change. The menu checkbox enables
or disables that task. If Windows refuses because the task was registered from an elevated shell,
the tray sends a notification telling the owner to run the script elevated, and leaves the
checkbox as it was. It doesn't fall back to a second startup method (two would start two trays).

## 6. Constraints

- Windows 11, Python 3.12+ (CI also runs 3.14), `pythonw`, no console windows.
- `serve` must keep working without the tray, exactly as now. The only additions on the `serve`
  side are extra `meta` writes, the token file, and loopback-only admin routes that need the token.
- No paid services, and no new network traffic to VECO: Run now is one extra check, made only
  when the owner asks.
- Only the `[tray]` extra adds dependencies.
- Develop in the `feat/tray-app` worktree. The main checkout stays on `main` because `serve` runs
  from it and only publishes from `main`.

## 7. Left to the builder

- How the tray tells a lock-held exit apart from a crash (a distinct exit code from `serve` is the
  obvious way).
- Icon drawing (a coloured dot drawn with Pillow at runtime, no image files), the 30 s polling
  thread, and how `pystray` menu items are built dynamically.
- Whether Enable/Disable-ScheduledTask works without elevation on this PC: check it once, early.
- Tests: the state and notification rules as pure functions over a status snapshot; supervision
  with a fake process; the admin routes with FastAPI's TestClient (token, Origin, 409). No test
  imports `pystray`.
