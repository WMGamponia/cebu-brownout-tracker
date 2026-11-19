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
    """Answers git by subcommand; `branch` per folder name; `fail` = {subcommand: GitResult}."""

    def __init__(self, root_branch="main", site_branch="gh-pages", dirty="", fail=None):
        self.calls: list[tuple[str, list[str]]] = []
        self.root_branch, self.site_branch, self.dirty = root_branch, site_branch, dirty
        self.fail = fail or {}

    def __call__(self, cwd: Path, args: list[str]) -> GitResult:
        self.calls.append((cwd.name, args))
        if args[0] in self.fail:
            return self.fail[args[0]]
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return GitResult(
                0, (self.site_branch if cwd.name == "site" else self.root_branch) + "\n", ""
            )
        answers = {
            "status": self.dirty,
            "rev-parse": "prev123\n",
            "write-tree": "tree456\n",
            "commit-tree": "new789\n",
        }
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


def _pub(home, git, start="main"):
    return Publisher(home, PublishSettings(home.root / "site"), start, git=git)


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
    assert tuple(
        next(a for _, a in git.calls if a[0] == "status")
    ) == ("status", "--porcelain", "--untracked-files=no", "--", "src/outages")


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
    assert not [a for _, a in git.calls if a[0] == "reset"]


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


def test_run_git_timeout(monkeypatch, tmp_path):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=publish.GIT_TIMEOUT_S)

    monkeypatch.setattr(publish.subprocess, "run", slow)
    r = publish.run_git(tmp_path, ["push"])
    assert r == GitResult(124, "", "timed out after 120 s")


def test_run_git_never_prompts(monkeypatch, tmp_path):
    seen = {}

    def fake(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

    monkeypatch.setattr(publish.subprocess, "run", fake)
    assert publish.run_git(tmp_path, ["status"]) == GitResult(0, "ok\n", "")
    assert seen["cmd"] == ["git", "status"] and seen["cwd"] == tmp_path
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0" and seen["timeout"] == 120


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
    assert p is not None and p.start_branch == "feat/z"


# --- real git, against a bare "origin" in tmp_path --------------------------------------------


def _git(cwd, *args) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout.strip()


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
    pub = Publisher(paths, PublishSettings(root / "site"), "main")
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
    o = Publisher(Paths(root), PublishSettings(root / "site"), "main").publish(NOW)
    assert o.status == "failed"
    assert o.note.keys == (f"publish:fail:{DAY}",)
    assert _git(root / "site", "rev-parse", "HEAD") == before
    assert _git(origin, "rev-parse", "gh-pages") == main_sha
