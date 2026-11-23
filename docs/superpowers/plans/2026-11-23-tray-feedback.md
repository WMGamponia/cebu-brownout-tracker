# Tray Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every tray action answers with a notification. A run in progress shows as a blue icon with its step. The menu shows whether the last run used Ollama. Two checkboxes start Ollama for scheduled runs and for Run now, and close it afterwards if the run started it.

**Architecture:** `serve` tracks the current run (kind, start time, step) and serves it at `/admin/status`. It records the street-suggestion outcome in `meta` next to the publish outcome, and keeps two Ollama settings in `meta` that it reads at the suggestion step. A new `outages.ollama` module probes, starts and stops Ollama. On the tray side, the pure rules add a blue state and the Ollama line, a new pure `tray/feedback.py` builds notification texts, and `app.py` wires it together.

**Tech Stack:** Python 3.12+, FastAPI, httpx, APScheduler 3, pystray (existing).

**Spec:** `docs/superpowers/specs/2026-11-23-tray-feedback-design.md` (builds on `docs/superpowers/specs/2026-11-22-tray-app-design.md`)

## Global Constraints

- `serve` without the tray behaves as now; the new `meta` writes never raise (use `_record`).
- Scheduled runs that succeed send **no** notification. "Run finished" is only for a Run now the tray started.
- Ollama settings default **off**. `serve` stops only an Ollama process **it** started, via that process's tree.
- Ollama not running never changes the icon colour.
- Ollama start: `OLLAMA_URL` (`http://localhost:11434`), probe `GET /api/version`, wait up to 60 s. Executable: `OLLAMA_EXE` (via `dotenv_get`), else `ollama` on `PATH`, else `%LOCALAPPDATA%\Programs\Ollama\ollama.exe`. Start it with `CREATE_NO_WINDOW`.
- Blue `#2f6fd6`. Red outranks blue, and blue outranks grey, amber and green.
- Tray poll: every 5 s while a run is in progress or a manual run/restart is awaited, otherwise every 30 s.
- No pystray import outside `tray/app.py`. Commits: Conventional Commits, no AI attribution lines.
- Tests run from the worktree with `PYTHONPATH=src E:/Work/Projects/OutageTracker/.venv/Scripts/python.exe -m pytest -q`.

## Review Focus

1. **The tray polls `/admin/status` of an old serve without the route (404):** treat it as "not running", without crashing. Test in Task 4 (`test_status_404_is_idle`).
2. **Ollama started but the suggestion step raises:** the started Ollama is still stopped (`finally`). Test in Task 2 (`test_started_ollama_stopped_on_error`).
3. **The Ollama executable is missing:** the outcome is `unreachable` with detail "Ollama not found", and the run carries on. Test in Task 2 (`test_missing_exe_is_unreachable`).
4. **A run in progress when the tray starts** (no Run now clicked): blue, but no "Run finished" notification. Test in Task 3 (`test_finished_only_for_awaited_manual`).
5. **Old `meta` with no suggest keys:** the Ollama line reads "Ollama: no run yet", not a crash. Test in Task 3 (`test_ollama_line_none`).

---

### Task 1: Run progress, `/admin/status` and Ollama settings in `serve`

**Files:**
- Modify: `src/outages/runstatus.py`, `src/outages/__main__.py` (`_Runs`, `_scheduled_run`, `_run_once`, `ServeControl`), `src/outages/map/admin.py`
- Test: `tests/test_runstatus.py`, `tests/test_scheduler.py`, `tests/test_map_admin.py`

**Interfaces:**
- Produces:
  - `RunStatus` gains `suggest_status: str | None = None` and `suggest_detail: str | None = None` as its last fields, so existing positional constructions keep working. `read` fills them from `last_suggest_status` / `last_suggest_detail`.
  - `runstatus.SuggestStatus = Literal["used", "not_needed", "unreachable", "skipped", "failed"]`
  - `runstatus.record_suggest(conn, status: SuggestStatus, detail: str, now: datetime) -> None`
  - `runstatus.Settings(ollama_scheduled: bool, ollama_manual: bool)` (frozen dataclass)
  - `runstatus.read_settings(conn) -> Settings`, with keys `ollama_autostart_scheduled` / `ollama_autostart_manual`, `"1"` = on, missing = off
  - `runstatus.write_settings(conn, s: Settings) -> None`
  - `_scheduled_run(..., progress: Callable[[str], None] | None = None)`. `_run_once` calls `progress(step)` with `"check"` (only when `do_check`), `"alerts"`, `"publish"` and `"suggest"` before each step it actually runs.
  - `_Runs.current: dict | None`, set to `{"kind", "started_at", "step"}` (ISO UTC `started_at`) for the length of every run (scheduled, watchdog, catch_up, manual) and cleared in `finally`. `_Runs.status() -> dict` returns `{"running": bool, "kind", "started_at", "step"}`, with Nones when idle.
  - `AdminControl` gains:
    - `status() -> dict`
    - `get_settings() -> dict` (`{"ollama_scheduled": bool, "ollama_manual": bool}`)
    - `set_settings(values: dict) -> dict` (partial update; returns the new full dict)
  - Routes: `GET /admin/status` → 200 dict; `GET /admin/settings` → 200 dict; `POST /admin/settings` → 200 new dict, or 400 when any key is unknown or not a bool. All use the same guard as the other admin routes.

- [ ] **Step 1: Failing tests.**
  - `test_runstatus.py`:
    - `test_record_suggest_roundtrip`
    - `test_old_status_has_no_suggest` (fresh DB → `suggest_status is None`)
    - `test_settings_default_off`
    - `test_settings_roundtrip`
  - `test_scheduler.py`:
    - `test_progress_steps_full_run`: with a publisher, steps are `["check", "alerts", "publish", "suggest"]`.
    - `test_progress_steps_alerts_only`: `do_check=False`, steps are `["alerts", "suggest"]`.
    - `test_runs_current_during_and_after`: inside a fake `_scheduled_run`, `runs.status()["running"] is True` with `kind == "scheduled"`; after it, `{"running": False, ...}`.
    - `test_manual_run_current_kind_manual`
    - `test_control_settings_roundtrip`: `set_settings({"ollama_manual": True})` → `get_settings() == {"ollama_scheduled": False, "ollama_manual": True}`, persisted in the DB.
  - `test_map_admin.py`:
    - `test_status_route`
    - `test_settings_get_and_post`
    - `test_settings_bad_body_400` (parametrised: unknown key, non-bool value, list body)
    - `test_status_needs_token`
  - Extend `FakeControl` with `status`, `get_settings` and `set_settings`.
- [ ] **Step 2: Run them.** `pytest tests/test_runstatus.py tests/test_scheduler.py tests/test_map_admin.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** as in Interfaces. `_Runs` wraps each `_scheduled_run` call it makes so that `current` is set with a `progress` callback that updates `current["step"]`.
- [ ] **Step 4: Run.** Full suite, `ruff check`, `ruff format --check`. Expected: pass.
- [ ] **Step 5: Commit.** `feat(serve): run progress, /admin/status and Ollama settings`

---

### Task 2: Ollama outcome, start and stop around street suggestions

**Files:**
- Create: `src/outages/ollama.py`
- Modify: `src/outages/streets/suggest.py` (`suggest_new`), `src/outages/__main__.py` (`_suggest_hook`, `_run_once`)
- Test: `tests/test_ollama.py`, `tests/test_streets_queue.py`, `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `runstatus.record_suggest`, `read_settings` (Task 1).
- Produces:
  - `street_suggest.Outcome(status: SuggestStatus, detail: str, summary: RunSummary | None)` (frozen).
  - `street_suggest.suggest_outcome(paths, now, picker=None, boundaries=None, budget_groups=..., budget_s=...) -> Outcome`. `suggest_new` keeps its signature and returns `suggest_outcome(...).summary`. Mapping:
    - each existing inert `return None` → `skipped` with its log reason as detail (`"inert until … FROZEN exists"`, `"no OSM extract"`, `"no database"`, `"no street_matches"`);
    - `LLMUnavailable` → `unreachable`, `str(e)`;
    - any other exception → `failed`, first line of `str(e)`;
    - a finished run with `new > 0` → `used`, `"{new} new street suggestions"`; otherwise `not_needed`, `""`.
  - `outages.ollama`:
    - `reachable(url: str = OLLAMA_URL, timeout: float = 2.0) -> bool`
    - `find_exe() -> Path | None`
    - `class Started: pid: int; stop() -> None` (kills the process tree hidden, as `tray.supervisor.kill_pid_tree` does; reuse that function by moving it to `outages/proc.py` and importing it in both places).
    - `ensure_running(wait_s: float = 60, *, reachable=reachable, find_exe=find_exe, spawn=_spawn, sleep=time.sleep, clock=time.monotonic) -> Started | None | str`:
      - `None` when Ollama was already answering;
      - `Started` when this call started it and it now answers;
      - the string `"Ollama not found"` / `"Ollama didn't start within 60 s"` otherwise. On the timeout it stops what it started.
  - `_suggest_hook(paths, settings, kind: RunKind = "scheduled")`:
    1. Reads `read_settings`. `kind == "manual"` uses `ollama_manual`; the other kinds use `ollama_scheduled`.
    2. When on, calls `ensure_running()`. A string result → record `unreachable` with that string and return, without suggesting.
    3. Runs `suggest_outcome`, records it with `record_suggest`. When this run started Ollama, appends `" (started for this run, then closed)"` to the detail.
    4. Keeps the admin note behaviour unchanged.
    5. Stops the started Ollama in `finally`.
  - `_run_once` passes `kind` to `_suggest_hook`. When `suggest` is False and `do_check`, it records `skipped` with `"street suggestions off (--no-suggest)"`.

- [ ] **Step 1: Failing tests.**
  - `test_ollama.py` (all injected, no real process or network):
    - `test_already_running_returns_none`
    - `test_starts_and_waits`: reachable False twice, then True → `Started`; spawn called once.
    - `test_timeout_stops_and_reports`
    - `test_missing_exe_is_unreachable_message`
    - `test_find_exe_env_override` (monkeypatch `dotenv_get`)
  - `test_streets_queue.py`:
    - `test_outcome_used`
    - `test_outcome_not_needed`
    - `test_outcome_unreachable` (a picker whose `check` raises `LLMUnavailable`)
    - `test_outcome_skipped_no_extract`
    - `test_suggest_new_still_returns_summary`
  - `test_scheduler.py`:
    - `test_suggest_records_outcome`
    - `test_manual_uses_manual_setting`: manual on and scheduled off; the manual run calls `ensure_running`, the scheduled run doesn't.
    - `test_catch_up_uses_scheduled_setting`
    - `test_started_ollama_stopped_on_error`: `suggest_outcome` raises, and `Started.stop` is still called.
    - `test_missing_exe_is_unreachable`: the outcome is `unreachable` with "Ollama not found", and the run completes.
    - `test_closed_note_in_detail`
    - `test_no_suggest_records_skipped`
- [ ] **Step 2: Run.** Expected: FAIL (`ModuleNotFoundError: outages.ollama`, missing names).
- [ ] **Step 3: Implement.** `_spawn(exe)` runs `subprocess.Popen([exe, "serve"], creationflags=CREATE_NO_WINDOW, stdio=DEVNULL)` and returns `Started(pid)`.
- [ ] **Step 4: Run.** Full suite plus ruff. Expected: pass.
- [ ] **Step 5: Commit.** `feat(serve): start and close Ollama for runs; record the suggestion outcome`

---

### Task 3: Tray rules: blue running state, Ollama line, notification texts

**Files:**
- Modify: `src/outages/tray/state.py`
- Create: `src/outages/tray/feedback.py`
- Test: `tests/test_tray_state.py`, `tests/test_tray_feedback.py`

**Interfaces:**
- Consumes: `RunStatus.suggest_*` (Task 1), the `/admin/status` dict shape (Task 1).
- Produces:
  - `Snapshot` gains `run: dict | None = None` (the `/admin/status` dict when it was running, else None).
  - `Colour` adds `"blue"`. Order: red > blue > grey > amber > green.
  - Blue headline: `"Running: {step} ({m}:{ss})"`, elapsed from `started_at` to `now`; a missing step reads `"starting"`.
  - `View.can_run: bool`, False while running (the menu greys out Run now).
  - `lines` gets an Ollama line after the publish line, from `suggest_status`/`suggest_detail`:
    - `used` → `"Ollama: used · {detail}"`
    - `not_needed` → `"Ollama: not needed this run"`
    - `unreachable` → `"Ollama: not running · street suggestions skipped"`
    - `skipped` → `"Ollama: not used · {detail}"`
    - `failed` → `"Ollama: street suggestions failed · {detail}"`
    - None → `"Ollama: no run yet"`
    - The detail is clipped by `_detail`.
  - `menu_key(view, pending, autostart, proc, busy, settings: dict | None = None)` includes `settings`.
  - `tray/feedback.py` (pure, returns `str`):
    - `run_started() -> "Run started"`
    - `run_busy(step: str | None, elapsed_s: int) -> "A run is already in progress ({step}, {m}:{ss})"`
    - `run_finished(s: RunStatus) -> "Run finished: OK · published abc1234 · Ollama used"`. The text joins: the check result (`OK` / `check failed`), `_publish_short`, and `Ollama used` / `Ollama not running` / `Ollama not needed` / `Ollama not used`.
    - `decided(name: str, approve: bool, result: str)`: `ok` → `"{name} approved"` / `"{name} denied"`; `not_pending` → `"{name}: already decided"`; `error` → `"Couldn't reach serve to decide {name}'s request"`.
    - `restarting() -> "Restarting serve…"`
    - `restarted() -> "serve restarted"`
    - `cant_restart(pid: int | None)` → `"Can't restart: this serve wasn't started by the tray and doesn't answer (PID {pid}). End it in Task Manager; the tray then starts a new one."` (`PID unknown` when None)
    - `autostart(on: bool) -> "Start at logon: on"` / `off`
    - `ollama_setting(which: str, on: bool) -> "Start Ollama for {which} runs: on"` / `off` (`which` is `scheduled` or `Run now`)
  - `should_announce_finish(awaited_since: datetime | None, status: RunStatus, running: bool) -> bool`: True only when `awaited_since` is set, nothing is running, `status.last_run_kind == "manual"` and `status.last_run_at >= awaited_since`.

- [ ] **Step 1: Failing tests.**
  - `test_tray_state.py`:
    - `test_running_blue_headline` (step `publish`, started 42 s ago → `"Running: publish (0:42)"`)
    - `test_red_beats_blue`
    - `test_blue_beats_amber`
    - `test_can_run_false_while_running`
    - `test_ollama_line_each_status` (parametrised)
    - `test_ollama_line_none`
    - `test_menu_key_includes_settings`
  - `test_tray_feedback.py`:
    - one test per text function with the exact strings above;
    - `test_finished_only_for_awaited_manual` (not awaited → False; a scheduled run → False; still running → False; a manual run newer than awaited → True).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Full suite plus ruff. Expected: pass.
- [ ] **Step 5: Commit.** `feat(tray): running state, Ollama line and notification texts`

---

### Task 4: Tray client, supervisor result, app wiring, docs

**Files:**
- Modify: `src/outages/tray/client.py`, `src/outages/tray/supervisor.py`, `src/outages/tray/app.py`, `README.md`, `ROADMAP.md`
- Test: `tests/test_tray_client.py`, `tests/test_tray_supervisor.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `ServeClient.status() -> dict | None`: None on error, 404 or a non-dict. The caller treats None as idle.
  - `ServeClient.settings() -> dict | None`
  - `ServeClient.set_settings(values: dict) -> dict | None`
  - `Supervisor.stop(...) -> Literal["stopped", "left"]`: `"left"` when an adopted holder didn't answer the stop request and was left running.
  - `Supervisor.restart_now(...)` returns the same value.
  - `app.TrayApp`:
    - Each action notifies through `feedback` and then wakes the worker so the icon refreshes at once.
    - It tracks `awaited_since` (set when Run now returns `started`) and `awaiting_restart`.
    - When `should_announce_finish` → notify `run_finished(status)` and clear `awaited_since`.
    - When `awaiting_restart` and `healthz` answers → notify `restarted()`.
    - On a `"left"` result → notify `cant_restart(lock_pid(data))`.
    - Polls every 5 s when `run` is set or anything is awaited.
    - Has the menu items "Start Ollama for scheduled runs" ✓ and "Start Ollama for Run now" ✓ (checked from `client.settings()`, greyed out while `serve` isn't answering).
    - Draws the blue icon colour `#2f6fd6`.

- [ ] **Step 1: Failing tests.**
  - `test_tray_client.py`:
    - `test_status_parses`
    - `test_status_404_is_idle`
    - `test_settings_roundtrip`
  - `test_tray_supervisor.py`:
    - `test_stop_returns_left_for_unanswering_adopted`
    - `test_stop_returns_stopped`
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** the client and supervisor, then wire `app.py`.
- [ ] **Step 4: Run.** Full suite plus ruff. Expected: pass.
- [ ] **Step 5: Manual check.** Run serve plus the tray on a copy of the DB on port 8799 (no `.env`). Then:
  - `/admin/status` while idle;
  - `/admin/settings` POST;
  - Run now through the API → `/admin/status` shows running with steps;
  - the meta suggest outcome is recorded.

  Don't start a real Ollama in the check unless it's already installed and idle. The settings stay off.
- [ ] **Step 6: Docs.**
  - README tray paragraph: the blue state, notifications for actions, the Ollama line and the two checkboxes.
  - ROADMAP: progress-log line dated 2026-11-23 and a "Tray feedback" section.
- [ ] **Step 7: Commit.** `feat(tray): action feedback, running state and Ollama toggles`
