# Tray feedback: every click answers

Date: 2026-11-23 · Branch: `feat/tray-feedback` · Status: draft for the owner's approval · Size: small change

## Intent (owner, 2026-11-23)

The tray from PR #22 gives no feedback on anything the owner does. Run now, Approve/Deny and
Restart serve are silent on success, and Restart serve was silent even when it did nothing (it
had adopted an old `serve` that couldn't take a stop request). Goal: every action visibly
answers, and a run in progress is visible while it happens.

## Approaches considered

| | How | Trade-off |
|---|---|---|
| **A (picked)** | Feedback in the tray: a notification for each action's result, a "running" icon state with progress in the menu, and a small `/admin/status` on `serve` for the run state | Fixes "I clicked and nothing happened" for every action. Both other options need the same run status anyway. |
| B | A tkinter status window (left-click): state, the last runs, buttons that update in place | A bigger piece of UI to build and maintain. It needs A's run status too. Deferred until the owner has used A for a while. |
| C | A status web page served by `serve` | Goes down exactly when `serve` does, which is when it's needed most. Rejected. |

## Design

**`serve` reports what it is doing.**
- `_Runs` keeps the current run: `kind`, `started_at`, and `step` (`check`, `alerts`, `publish`,
  `suggest`), set by `_run_once` as it moves between steps.
- New `GET /admin/status` returns `{"running": bool, "kind", "started_at", "step"}`, with the
  same token and no-Origin guard as the other admin routes.

**The tray answers every action with a notification.**

| Action | Notification |
|---|---|
| Run now | "Run started". Then, when that run ends: "Run finished: OK · published abc1234", or its problem. |
| Run now while a run is in progress | "A run is already in progress (check, 0:42)" (exists today, now with the step) |
| Approve / Deny | "Juan approved" / "Juan denied", or "already decided" / "couldn't reach serve" |
| Restart serve / Start serve | "Restarting serve…", then "serve restarted" once it answers `/healthz` |
| Restart on an adopted `serve` that won't stop | "Can't restart: this serve wasn't started by the tray and doesn't answer (PID 2356). End it in Task Manager; the tray then starts a new one." |
| Start at logon | "Start at logon: on" / "off" (denied/missing messages exist today) |

A scheduled run that succeeds still sends no notification. That anti-goal from the tray spec
stands. The "Run finished" notification is only for a run the owner started.

**Run in progress.**
- A fifth icon colour, **blue**, shows that a run is in progress. Red still wins: a broken state
  stays red even mid-run.
- The menu headline reads "Running: publish (0:42)". Run now is greyed out during a run.
- The tray re-reads status every 5 s while a run is in progress (otherwise every 30 s), so the
  finish notification arrives within seconds.

**Whether the last run used Ollama (owner, 2026-11-23).** Ollama is used by one step only:
street suggestions, after the alerts. Area parsing uses the rules extractor. That step's outcome
goes into `meta` like the publish outcome (`last_suggest_status`, `last_suggest_detail`), written
only by runs that reach it:

| `last_suggest_status` | Menu line |
|---|---|
| `used` (Ollama was asked) | "Ollama: used · 3 new street suggestions" |
| `not_needed` (ran, nothing new to ask) | "Ollama: not needed this run" |
| `unreachable` | "Ollama: not running · street suggestions skipped" |
| `skipped` (no OSM extract, not frozen, `--no-suggest`, …) | "Ollama: not used · <reason>" |
| `failed` | "Ollama: street suggestions failed · <first line>" |

My call: an unreachable Ollama **doesn't change the icon colour** and doesn't send its own
notification, because suggestions are optional and alerts aren't affected. It does appear in the
menu, and in the "Run finished" notification for a run you started ("… · Ollama not running").

**Start Ollama for runs (owner, 2026-11-23).** Two separate menu checkboxes, both off by default
(owner):
- **Start Ollama for scheduled runs** covers the 06:00 and 20:00 runs. My call: it also covers
  the watchdog's catch-up of a missed run and `serve`'s start-up catch-up, since those stand in
  for a scheduled run.
- **Start Ollama for Run now** covers runs you start from the tray.

How it's stored and read:
- The settings live in `meta` (`ollama_autostart_scheduled`, `ollama_autostart_manual`). The tray
  sets them through a new admin route, `POST /admin/settings`, so `serve` stays the only writer
  of the database.
- `serve` reads the setting that matches the run's kind at the start of the suggestions step.
- When it's on and `http://localhost:11434` doesn't answer, `serve` starts `ollama.exe serve`
  hidden (`CREATE_NO_WINDOW`), from `PATH` or `%LOCALAPPDATA%\Programs\Ollama\ollama.exe`
  (`OLLAMA_EXE` in `.env` overrides). It waits up to 60 s for `/api/version`. If Ollama doesn't
  come up, that counts as `unreachable`, as today.
- **Closing it afterwards:** when the suggestions step ends, `serve` stops the Ollama it started
  (its process tree), which also frees the VRAM straight away. It **never** stops an Ollama it
  didn't start, such as the one `Ollama.lnk` starts at logon or one you opened yourself.
- `last_suggest_detail` records it, and the menu line shows it: "Ollama: used (started for this
  run, then closed) · 3 new street suggestions".

Risks:
- Loading `qwen3:14b` takes the GPU for 10–30 s plus the suggestion time, so a game running then
  may stutter. The owner has said occasional lag is acceptable.
- The run is longer. The blue "running" state shows it.
- If the owner opens the Ollama app while the run's own Ollama holds port 11434, the app may
  complain until the run ends.

**Fix in passing:** Restart/Start serve, Approve/Deny and Run now each refresh the icon at once,
so the result shows without waiting for the next refresh.

## Non-goals

A window (approach B), run history, log viewing, and changes to how runs or alerts work.

## Left to the builder

- How the tray matches "the run I started" to its end. Suggested: the `started_at` returned by
  `/admin/run`, or the first `/admin/status` after it.
- Exact wording. Keep it short, since Windows shows about two lines.
- Tests: `/admin/status` with TestClient; the rules for blue and the headline in `state.py`, kept
  pure; the action-to-notification mapping as a pure function where possible.
