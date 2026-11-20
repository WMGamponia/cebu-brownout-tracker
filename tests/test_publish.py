"""The publisher (live-site spec 5.2, 5.3): the gate, the one-commit push with lease, and the
notes it hands back. Git is faked, or real against a bare repo in tmp_path; nothing leaves it."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from outages import publish, snapshot_run
from outages.config import Paths, PublishSettings, publish_settings
from outages.publish import GitResult, Publisher
from outages.snapshot_run import Taken
from outages.timeparse import MANILA

NOW = datetime(2026, 11, 19, 20, 1, 5, tzinfo=MANILA)
DAY = "2026-11-19"


class FakeGit:
    """Answers git by subcommand; per-folder answers for branch and HEAD; `fail` maps a
    subcommand to a GitResult."""

    def __init__(
        self,
        root_branch="main",
        site_branch="gh-pages",
        dirty="",
        untracked="",
        root_head="main123",
        remote="prev123",
        fail=None,
    ):
        self.calls: list[tuple[str, list[str]]] = []
        self.root_branch, self.site_branch = root_branch, site_branch
        self.dirty, self.untracked, self.root_head, self.remote = (
            dirty,
            untracked,
            root_head,
            remote,
        )
        self.fail = fail or {}

    def __call__(self, cwd: Path, args: list[str]) -> GitResult:
        self.calls.append((cwd.name, args))
        site = cwd.name == "site"
        if args[0] in self.fail:
            return self.fail[args[0]]
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return GitResult(0, (self.site_branch if site else self.root_branch) + "\n", "")
        if args == ["rev-parse", "HEAD"]:
            return GitResult(0, ("prev123" if site else self.root_head) + "\n", "")
        if args[0] == "status":
            return GitResult(0, self.untracked if "--ignored" in args else self.dirty, "")
        if args[0] == "ls-remote":
            return GitResult(0, f"{self.remote}\trefs/heads/gh-pages\n" if self.remote else "", "")
        answers = {"write-tree": "tree456\n", "commit-tree": "new789\n"}
        return GitResult(0, answers.get(args[0], ""), "")


@pytest.fixture
def home(tmp_path):
    (tmp_path / "site").mkdir()
    return Paths(tmp_path)


def _taken(monkeypatch, code=0, error=None):
    calls = []

    def fake(paths, out, now, allow_old=False):
        calls.append(out)
        if code:
            return Taken(code, error)
        (out / "snapshot.json").write_text("{}", encoding="utf-8")
        return Taken(0, None, taken_at=now.astimezone(MANILA).replace(microsecond=0))

    monkeypatch.setattr(snapshot_run, "take", fake)
    return calls


def _pub(home, git, start="main", start_commit="main123"):
    return Publisher(
        home, PublishSettings(home.root / "site"), start, git=git, start_commit=start_commit
    )


def test_skip_branch_at_start(home, monkeypatch):
    calls = _taken(monkeypatch)
    o = _pub(home, FakeGit(), start="feat/x").publish(NOW)
    assert o.status == "skipped" and not calls
    assert o.note.keys == (f"publish:skip:branch:{DAY}",)
    assert o.note.text == "Site not published: serve is on feat/x, not main."


def test_skip_branch_now(home, monkeypatch):
    calls = _taken(monkeypatch)
    o = _pub(home, FakeGit(root_branch="feat/y")).publish(NOW)
    assert o.status == "skipped" and not calls
    assert o.note.keys == (f"publish:skip:branch:{DAY}",)
    assert "feat/y" in o.note.text


def test_skip_dirty(home, monkeypatch):
    calls = _taken(monkeypatch)
    git = FakeGit(dirty=" M src/outages/map/build.py\n")
    o = _pub(home, git).publish(NOW)
    assert o.status == "skipped" and not calls
    assert o.note.keys == (f"publish:skip:dirty:{DAY}",)
    assert o.note.text == "Site not published: uncommitted changes in src/outages."
    assert tuple(next(a for _, a in git.calls if a[0] == "status")) == (
        "status",
        "--porcelain",
        "--untracked-files=no",
        "--",
        "src/outages",
    )


def test_take_failure_is_failed(home, monkeypatch):
    _taken(monkeypatch, code=2, error="data is 16 h old; run check first or pass --allow-old")
    git = FakeGit()
    o = _pub(home, git).publish(NOW)
    assert o.status == "failed"
    assert o.note.keys == (f"publish:fail:{DAY}",)
    assert (
        o.note.text == "Site publish failed: data is 16 h old; run check first or pass --allow-old"
    )
    assert not [a for _, a in git.calls if a[0] in ("add", "push")]


def test_publish_fails_when_site_not_gh_pages(home, monkeypatch):
    calls = _taken(monkeypatch)
    o = _pub(home, FakeGit(site_branch="main")).publish(NOW)
    assert o.status == "failed" and not calls
    assert o.note.keys == (f"publish:fail:{DAY}",)
    assert "gh-pages" in o.note.text


def test_publish_fails_when_site_missing(tmp_path, monkeypatch):
    calls = _taken(monkeypatch)
    o = _pub(Paths(tmp_path), FakeGit()).publish(NOW)
    assert o.status == "failed" and not calls


def test_git_commands_in_order(home, monkeypatch):
    _taken(monkeypatch)
    git = FakeGit()
    o = _pub(home, git).publish(NOW)
    assert o.status == "published" and o.note is None
    site = [a for cwd, a in git.calls if cwd == "site"]
    assert site == [
        ["rev-parse", "--abbrev-ref", "HEAD"],
        ["rev-parse", "HEAD"],
        ["add", "-A"],
        ["write-tree"],
        ["commit-tree", "tree456", "-m", "snapshot: 2026-11-19T20:01:05+08:00"],
        ["ls-remote", "origin", "refs/heads/gh-pages"],
        ["push", "--force-with-lease=gh-pages:prev123", "origin", "new789:refs/heads/gh-pages"],
        ["reset", "--soft", "new789"],
    ]


@pytest.mark.parametrize("step", ["add", "write-tree", "commit-tree", "push"])
def test_a_failing_git_step_stops_the_rest(home, monkeypatch, step):
    _taken(monkeypatch)
    git = FakeGit(fail={step: GitResult(1, "", f"fatal: {step} broke\nmore")})
    o = _pub(home, git).publish(NOW)
    assert o.status == "failed"
    assert o.note.text == f"Site publish failed: fatal: {step} broke"
    assert not [a for _, a in git.calls if a[:2] == ["reset", "--soft"]]


def test_timeout_becomes_failed(home, monkeypatch):
    _taken(monkeypatch)
    git = FakeGit(fail={"push": GitResult(124, "", "timed out after 120 s")})
    o = _pub(home, git).publish(NOW)
    assert o.status == "failed" and "timed out after 120 s" in o.note.text


def test_unexpected_error_is_failed_not_raised(home, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(snapshot_run, "take", boom)
    o = _pub(home, FakeGit()).publish(NOW)
    assert o.status == "failed" and "disk full" in o.note.text


def test_run_cmd_timeout_with_a_child_holding_the_pipes(tmp_path):
    """C1: a git child (remote-https, the credential manager) that outlives git must not hold the
    run past its timeout."""
    import sys
    import time

    grandchild = "import time; time.sleep(60)"
    code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}]); time.sleep(60)"
    )
    t0 = time.monotonic()
    r = publish._run_cmd([sys.executable, "-c", code], tmp_path, None, timeout=2)
    assert r == GitResult(124, "", "timed out after 2 s")
    assert time.monotonic() - t0 < 15


def test_run_cmd_output_and_exit_code(tmp_path):
    import sys

    code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
    r = publish._run_cmd([sys.executable, "-c", code], tmp_path, None, timeout=30)
    assert (r.code, r.out.strip(), r.err.strip()) == (3, "out", "err")


@pytest.mark.skipif(not hasattr(subprocess, "CREATE_NO_WINDOW"), reason="Windows only")
def test_no_window_is_create_no_window_on_windows():
    assert publish.NO_WINDOW == subprocess.CREATE_NO_WINDOW


def test_run_cmd_and_kill_tree_open_no_console_window(monkeypatch, tmp_path):
    """Under pythonw a console child gets a window of its own, which flashed over a fullscreen
    game at 20:00 on 2026-11-19 (and git commit-tree died with 0xC000013A)."""
    seen = []

    class FakePopen:
        pid = 4242

        def __init__(self, cmd, **kw):
            seen.append(("popen", kw.get("creationflags")))

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("git", timeout)

        def kill(self):
            pass

    def fake_run(cmd, **kw):
        seen.append(("run", kw.get("creationflags")))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(publish.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(publish.subprocess, "run", fake_run)
    monkeypatch.setattr(publish.os, "killpg", lambda *a: None, raising=False)
    publish._run_cmd(["git", "status"], tmp_path, None, timeout=1)
    assert seen[0] == ("popen", publish.NO_WINDOW)
    assert all(flags == publish.NO_WINDOW for _, flags in seen if _ == "run")


def test_run_git_never_prompts(monkeypatch, tmp_path):
    seen = {}

    def fake(cmd, cwd, env, timeout=publish.GIT_TIMEOUT_S):
        seen.update(cmd=cmd, cwd=cwd, env=env, timeout=timeout)
        return GitResult(0, "ok\n", "")

    monkeypatch.setattr(publish, "_run_cmd", fake)
    assert publish.run_git(tmp_path, ["status"]) == GitResult(0, "ok\n", "")
    assert seen["cmd"] == [
        "git",
        "-c",
        "credential.interactive=never",
        "-c",
        "http.lowSpeedLimit=1000",
        "-c",
        "http.lowSpeedTime=60",
        "status",
    ]
    assert seen["cwd"] == tmp_path and seen["timeout"] == 120
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0" and seen["env"]["GCM_INTERACTIVE"] == "never"


def test_skip_untracked_static(home, monkeypatch):
    """I2: an untracked or ignored file under the static folder would be published."""
    calls = _taken(monkeypatch)
    git = FakeGit(untracked="?? src/outages/map/static/js/map.js.bak\n")
    o = _pub(home, git).publish(NOW)
    assert o.status == "skipped" and not calls
    assert o.note.keys == (f"publish:skip:dirty:{DAY}",)
    assert [
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--ignored",
        "--",
        "src/outages/map/static",
    ] in [a for _, a in git.calls]


def test_skip_when_main_moved_since_start(home, monkeypatch):
    """I3: Python loaded at start, pages read from disk now: both must be the same commit."""
    calls = _taken(monkeypatch)
    o = _pub(home, FakeGit(root_head="newer99")).publish(NOW)
    assert o.status == "skipped" and not calls
    assert o.note.keys == (f"publish:skip:moved:{DAY}",)
    assert o.note.text == "Site not published: main moved since serve started; restart serve."


def test_fail_note_names_the_error_not_the_url(home, monkeypatch):
    """M1: git puts "To <url>" first on a refused push."""
    _taken(monkeypatch)
    err = (
        "To https://github.com/o/r.git\n"
        " ! [rejected]        new789 -> gh-pages (stale info)\n"
        "error: failed to push some refs to 'https://github.com/o/r.git'\n"
    )
    o = _pub(home, FakeGit(fail={"push": GitResult(1, "", err)})).publish(NOW)
    assert o.note.text == "Site publish failed: ! [rejected]        new789 -> gh-pages (stale info)"


def test_failure_after_take_restores_the_site(home, monkeypatch):
    """I1: the working tree goes back to what is live, so the next publish keeps its folders."""
    _taken(monkeypatch)
    git = FakeGit(fail={"push": GitResult(1, "", "fatal: network")})
    o = _pub(home, git).publish(NOW)
    assert o.status == "failed"
    site = [a for cwd, a in git.calls if cwd == "site"]
    assert site[-2:] == [["reset", "-q", "--hard", "HEAD"], ["clean", "-fdq"]]


def test_publish_settings(tmp_path, monkeypatch):
    for k in ("PUBLISH_SITE", "PUBLISH_DIR"):
        monkeypatch.delenv(k, raising=False)
    assert publish_settings(tmp_path) is None
    (tmp_path / ".env").write_text("PUBLISH_SITE=0\n", encoding="utf-8")
    assert publish_settings(tmp_path) is None
    (tmp_path / ".env").write_text("PUBLISH_SITE=1\n", encoding="utf-8")
    assert publish_settings(tmp_path) == PublishSettings(tmp_path / "site")
    (tmp_path / ".env").write_text("PUBLISH_SITE=1\nPUBLISH_DIR=out\n", encoding="utf-8")
    assert publish_settings(tmp_path) == PublishSettings(tmp_path / "out")


def test_make_publisher(tmp_path, monkeypatch):
    monkeypatch.delenv("PUBLISH_SITE", raising=False)
    assert publish.make_publisher(Paths(tmp_path), git=FakeGit()) is None
    monkeypatch.setenv("PUBLISH_SITE", "1")
    p = publish.make_publisher(Paths(tmp_path), git=FakeGit(root_branch="feat/z"))
    assert p is not None and p.start_branch == "feat/z" and p.start_commit == "main123"


# --- real git, against a bare "origin" in tmp_path --------------------------------------------


def _git(cwd, *args) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def _head(cwd) -> str:
    return _git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    """main with src/outages/a.py, a 2-commit gh-pages worktree at repo/site, both on origin."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    (root / "src" / "outages").mkdir(parents=True)
    (root / "src" / "outages" / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "worktree", "add", "-q", "-b", "gh-pages", "site")
    _git(root / "site", "rm", "-rq", "src")  # gh-pages holds the site only
    for n in (1, 2):
        (root / "site" / "old.txt").write_text(f"{n}\n", encoding="utf-8")
        _git(root / "site", "add", "-A")
        _git(root / "site", "commit", "-q", "-m", f"hand {n}")
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "push", "-q", "origin", "main", "gh-pages")
    return root, origin


def test_push_real_bare_repo(repo, monkeypatch):
    root, origin = repo
    _taken(monkeypatch)
    paths = Paths(root)
    pub = Publisher(paths, PublishSettings(root / "site"), "main", start_commit=_head(root))
    for _ in range(2):
        o = pub.publish(NOW)
        assert o.status == "published", o.detail
        assert _git(origin, "rev-list", "--count", "gh-pages") == "1"
        assert _git(origin, "rev-list", "--parents", "-n", "1", "gh-pages").count(" ") == 0
        assert _git(origin, "rev-parse", "gh-pages") == _git(root / "site", "rev-parse", "HEAD")
        files = _git(origin, "ls-tree", "--name-only", "gh-pages").split()
        assert files == ["old.txt", "snapshot.json"]  # take() leaves other files to snapshot.write
    assert _git(origin, "log", "-1", "--format=%s", "gh-pages") == (
        "snapshot: 2026-11-19T20:01:05+08:00"
    )


def test_push_refused_when_remote_moved(repo, monkeypatch):
    root, origin = repo
    _taken(monkeypatch)
    main_sha = _git(root, "rev-parse", "main")
    _git(root, "push", "-q", "--force", "origin", f"{main_sha}:refs/heads/gh-pages")
    before = _git(root / "site", "rev-parse", "HEAD")
    o = Publisher(
        Paths(root), PublishSettings(root / "site"), "main", start_commit=_head(root)
    ).publish(NOW)
    assert o.status == "failed"
    assert _git(root / "site", "status", "--porcelain") == ""  # restored to what is live
    assert o.note.keys == (f"publish:fail:{DAY}",)
    assert _git(root / "site", "rev-parse", "HEAD") == before
    assert _git(origin, "rev-parse", "gh-pages") == main_sha


def test_recovers_when_its_own_push_landed_but_reset_did_not(repo, monkeypatch):
    """M2: the push reached GitHub but git timed out locally, so local gh-pages still points at
    the commit before. The remote's head is our own snapshot commit: lease on it, don't stall."""
    root, origin = repo
    _taken(monkeypatch)
    site = root / "site"
    hand = _head(site)
    pub = Publisher(Paths(root), PublishSettings(site), "main", start_commit=_head(root))
    assert pub.publish(NOW).status == "published"
    _git(site, "reset", "-q", "--soft", hand)  # the lost local step
    o = pub.publish(NOW)
    assert o.status == "published", o.detail
    assert _git(origin, "rev-list", "--count", "gh-pages") == "1"
    assert _git(origin, "rev-parse", "gh-pages") == _head(site)


def test_restored_site_keeps_the_live_version_next_time(repo, monkeypatch):
    """I1 end to end with the real snapshot writer: publish N, a failed N+1, then N+2 must still
    hold N's data folder (the version visitors may have loaded)."""
    import json

    from outages import snapshot

    root, origin = repo
    site = root / "site"
    ids = iter(["20261119T060001", "20261119T200001", "20261120T060001"])

    def fake(paths, out, now, allow_old=False):
        i = next(ids)
        snap = {"format": 2, "data": f"data/{i}/", "static": "static/aaaaaaaaaa/"}
        snapshot.write(
            out,
            {
                "snapshot.json": json.dumps(snap).encode(),
                f"data/{i}/boundaries.geojson": b"{}",
                "static/aaaaaaaaaa/js/map.js": b"x",
            },
        )
        return Taken(0, None, taken_at=now)

    monkeypatch.setattr(snapshot_run, "take", fake)
    pub = Publisher(Paths(root), PublishSettings(site), "main", start_commit=_head(root))
    assert pub.publish(NOW).status == "published"
    _git(root, "remote", "set-url", "origin", str(root / "nowhere.git"))
    assert pub.publish(NOW).status == "failed"
    _git(root, "remote", "set-url", "origin", str(origin))
    assert pub.publish(NOW).status == "published"
    tree = _git(origin, "ls-tree", "-d", "--name-only", "gh-pages:data").split()
    assert tree == ["20261119T060001", "20261120T060001"]
