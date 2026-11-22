"""The tray's rules: status snapshot -> icon colour, menu lines, notifications (tray spec 5.3)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from outages.runstatus import RunStatus
from outages.timeparse import MANILA
from outages.tray.state import Snapshot, evaluate

DAY = datetime(2026, 11, 23, tzinfo=MANILA)  # a Monday


def at(hm: str, day: int = 0) -> datetime:
    h, m = hm.split(":")
    return DAY + timedelta(days=day, hours=int(h), minutes=int(m))


NOW = at("21:00")
GOOD = RunStatus(
    last_run_at=at("20:00") + timedelta(seconds=40),
    last_run_kind="scheduled",
    last_check_ok=True,
    last_check_at=at("20:00") + timedelta(seconds=30),
    publish_status="published",
    publish_detail="abc1234 snapshot 2026-11-23 20:00",
    publish_at=at("20:00") + timedelta(seconds=40),
)
NONE = RunStatus(None, None, None, None, None, None, None)


def snap(**kw) -> Snapshot:
    base = dict(
        now=NOW,
        proc="running",
        proc_since=at("06:00"),
        healthz_ok=True,
        status=GOOD,
        pending=[],
        crashed_at=None,
    )
    base.update(kw)
    return Snapshot(**base)


def keys(view) -> list[str]:
    return [e.key for e in view.events]


def test_green():
    v = evaluate(snap())
    assert v.colour == "green"
    assert v.events == []
    assert v.lines[0] == "Last run: Mon 23 Nov 20:00 · OK"
    assert v.lines[1] == "Publish: published abc1234 snapshot 2026-11-23 20:00"
    assert v.lines[2] == "Next run: Tue 24 Nov 06:00"


def test_publish_skipped_red():
    s = replace(GOOD, publish_status="skipped", publish_detail="not on main")
    v = evaluate(snap(status=s))
    assert v.colour == "red"
    assert keys(v) == [f"publish_skipped:{GOOD.last_run_at.isoformat()}"]
    assert "not on main" in v.events[0].text
    assert v.lines[1] == "Publish: skipped: not on main"


def test_publish_failed_red():
    s = replace(GOOD, publish_status="failed", publish_detail="push rejected")
    v = evaluate(snap(status=s))
    assert v.colour == "red" and v.events[0].text == "Publish failed: push rejected"


def test_failed_check_red_not_missed():
    s = replace(GOOD, last_check_ok=False, last_check_at=at("06:00"))
    v = evaluate(snap(status=s))
    assert v.colour == "red"
    assert keys(v) == [f"check_failed:{GOOD.last_run_at.isoformat()}"]
    assert v.lines[0] == "Last run: Mon 23 Nov 20:00 · check failed"


def test_new_failure_new_event_while_red():
    morning = replace(
        GOOD,
        last_run_at=at("06:00"),
        publish_status="skipped",
        publish_detail="not on main",
    )
    evening = replace(morning, last_run_at=at("20:00"), last_check_ok=False)
    k1 = set(keys(evaluate(snap(now=at("07:00"), status=morning))))
    k2 = set(keys(evaluate(snap(status=evening))))
    assert f"check_failed:{at('20:00').isoformat()}" in k2
    assert k1.isdisjoint(k2)


def test_missed_amber():
    s = replace(GOOD, last_run_at=at("06:00"), last_check_at=at("06:00"))
    v = evaluate(snap(now=at("20:15"), status=s))
    assert v.colour == "amber"
    assert keys(v) == [f"missed:{at('20:00').isoformat()}"]
    assert v.events[0].text == "Missed the 20:00 run"


def test_missed_not_before_slack():
    s = replace(GOOD, last_run_at=at("06:00"), last_check_at=at("06:00"))
    v = evaluate(snap(now=at("20:05"), status=s))
    assert v.colour == "green" and v.events == []


def test_starting_grey_then_not_answering_red():
    v = evaluate(snap(proc="starting", proc_since=NOW - timedelta(seconds=59), healthz_ok=False))
    assert v.colour == "grey" and v.events == []
    since = NOW - timedelta(seconds=61)
    v = evaluate(snap(proc="running", proc_since=since, healthz_ok=False))
    assert v.colour == "red"
    assert keys(v) == [f"not_answering:{since.isoformat()}"]
    v = evaluate(snap(proc="starting", proc_since=since, healthz_ok=False))
    assert v.colour == "red"


def test_capped_red():
    since = at("20:30")
    v = evaluate(snap(proc="capped", proc_since=since, healthz_ok=False))
    assert v.colour == "red"
    assert f"crash_cap:{since.isoformat()}" in keys(v)
    assert any("restarts stopped" in e.text for e in v.events)


def test_crash_restarted_event():
    crashed = at("20:50")
    v = evaluate(snap(proc="restarting", proc_since=crashed, healthz_ok=False, crashed_at=crashed))
    assert v.colour == "amber"
    assert keys(v) == [f"crash:{crashed.isoformat()}"]
    assert v.events[0].text == "serve crashed and was restarted"


def test_stopped_grey():
    s = replace(GOOD, last_run_at=at("06:00"), last_check_at=at("06:00"))
    v = evaluate(snap(proc="stopped", healthz_ok=False, status=s, now=at("20:30")))
    assert v.colour == "grey"
    assert v.events == []  # the owner stopped it: no "missed" nagging


def test_adopted_line():
    v = evaluate(snap(proc="adopted"))
    assert "serve: started elsewhere" in v.lines
    assert v.colour == "green"


def test_join_event_per_chat():
    pend = [{"chat_id": 7, "name": "Juan", "requested_at": ""}, {"chat_id": 8, "name": None}]
    v = evaluate(snap(pending=pend))
    assert keys(v) == ["join:7", "join:8"]
    assert v.events[0].text == "Join request from Juan: right-click to approve"
    assert v.events[1].text == "Join request from chat 8: right-click to approve"
    assert v.colour == "green"


def test_publish_off_not_red():
    s = replace(GOOD, publish_status="off", publish_detail="")
    v = evaluate(snap(status=s))
    assert v.colour == "green" and v.lines[1] == "Publish: off"


def test_no_status_yet():
    v = evaluate(snap(proc="running", proc_since=NOW - timedelta(seconds=30), status=NONE))
    assert v.colour == "grey"
    assert v.lines[0] == "Last run: never" and v.lines[1] == "Publish: none yet"
    v = evaluate(snap(proc="running", proc_since=NOW - timedelta(minutes=5), status=NONE))
    assert v.colour == "amber" and v.events == []


def test_stale_amber_no_event():
    s = replace(GOOD, last_check_at=NOW - timedelta(hours=16), last_run_at=at("20:00"))
    v = evaluate(snap(status=s))
    assert v.colour == "amber" and v.events == []


def test_tooltip_limit():
    long = replace(GOOD, publish_status="skipped", publish_detail="x" * 300)
    assert len(evaluate(snap(status=long)).tooltip) <= 127
    assert evaluate(snap()).tooltip.startswith("Last run 20:00 OK · published abc1234")


def test_can_act_follows_healthz():
    assert evaluate(snap()).can_act is True
    assert evaluate(snap(healthz_ok=False)).can_act is False


def test_headline_names_state():
    assert evaluate(snap()).headline == "Running fine"
    s = replace(GOOD, publish_status="skipped", publish_detail="not on main")
    assert evaluate(snap(status=s)).headline == "Publish skipped: not on main"
