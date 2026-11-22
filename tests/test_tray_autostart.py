"""Start at logon: the logon task's enabled flag (tray spec 5.8). No real PowerShell here."""

from __future__ import annotations

from outages.tray import autostart


def fake(code=0, out="", err=""):
    calls: list[str] = []

    def run(command: str):
        calls.append(command)
        return code, out, err

    return run, calls


def test_task_name_matches_install_script():
    from pathlib import Path

    script = (Path(__file__).parents[1] / "scripts" / "install-task.ps1").read_text("utf-8")
    assert f'$ServeTask = "{autostart.TASK_NAME}"' in script


def test_is_enabled_true_false_missing():
    run, calls = fake(out="Ready\r\n")
    assert autostart.is_enabled(run) is True
    assert autostart.TASK_NAME in calls[0]
    run, _ = fake(out="Disabled\r\n")
    assert autostart.is_enabled(run) is False
    run, _ = fake(code=1, err="No MSFT_ScheduledTask objects found")
    assert autostart.is_enabled(run) is None


def test_set_enabled_ok():
    run, calls = fake()
    assert autostart.set_enabled(True, run) == "ok"
    assert "Enable-ScheduledTask" in calls[0]
    run, calls = fake()
    assert autostart.set_enabled(False, run) == "ok"
    assert "Disable-ScheduledTask" in calls[0]


def test_set_enabled_denied():
    run, _ = fake(code=1, err="Access is denied. HRESULT 0x80070005")
    assert autostart.set_enabled(True, run) == "denied"


def test_set_enabled_missing_and_error():
    run, _ = fake(code=1, err="No MSFT_ScheduledTask objects found with property 'TaskName'")
    assert autostart.set_enabled(True, run) == "missing"
    run, _ = fake(code=1, err="something else")
    assert autostart.set_enabled(True, run) == "error"
