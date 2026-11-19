"""The live site (live-site spec 5.2, 5.3): after a successful check, `serve` builds the snapshot
into the gh-pages worktree and replaces gh-pages with one parentless commit, force-pushed with a
lease. Only from `main`, at the commit `serve` started on, with no tracked changes under
src/outages and nothing untracked under the static folder. Never raises: the outcome (and at most
one admin note per Manila day and reason) goes back to the run."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from outages import snapshot_run
from outages.alerts.run import Note
from outages.config import Paths, PublishSettings, publish_settings
from outages.timeparse import MANILA

log = logging.getLogger(__name__)

MAIN = "main"
SITE_BRANCH = "gh-pages"
STATIC_DIR = "src/outages/map/static"
GIT_TIMEOUT_S = 120
# Never ask anyone anything (Git Credential Manager has its own GUI prompt), and give up on a
# stalled transfer instead of waiting on it.
GIT_CONFIG = (
    "-c",
    "credential.interactive=never",
    "-c",
    "http.lowSpeedLimit=1000",
    "-c",
    "http.lowSpeedTime=60",
)


@dataclass(frozen=True)
class GitResult:
    code: int
    out: str
    err: str


Git = Callable[[Path, list[str]], GitResult]


def _kill_tree(p: subprocess.Popen) -> None:
    """Kill `p` and everything it started (git's remote helper, the credential manager)."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(p.pid)],
                capture_output=True,
                timeout=15,
            )
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        p.kill()
        p.wait(timeout=15)
    except (OSError, subprocess.SubprocessError):
        pass


def _run_cmd(
    cmd: list[str], cwd: Path, env: dict[str, str] | None, timeout: float = GIT_TIMEOUT_S
) -> GitResult:
    """Run `cmd` with its output in temporary files, not pipes: a child that outlives it can't
    hold the call open past `timeout` (review C1). On timeout the whole tree is killed."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            p = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                env=env,
                start_new_session=os.name != "nt",
            )
        except OSError as e:
            return GitResult(127, "", str(e))
        try:
            code = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(p)
            return GitResult(124, "", f"timed out after {timeout:g} s")
        out.seek(0)
        err.seek(0)
        return GitResult(
            code,
            out.read().decode("utf-8", "replace"),
            err.read().decode("utf-8", "replace"),
        )


def run_git(cwd: Path, args: list[str]) -> GitResult:
    """`git <args>` in `cwd`; never prompts for credentials, gives up after GIT_TIMEOUT_S."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    return _run_cmd(["git", *GIT_CONFIG, *args], cwd, env)


@dataclass(frozen=True)
class Outcome:
    status: Literal["published", "skipped", "failed"]
    detail: str
    note: Note | None


class _Failed(Exception):
    pass


def _first_line(error: str) -> str:
    """The line of git's stderr worth sending: not the "To <url>" line a refused push starts
    with (review M1)."""
    lines = [ln.strip() for ln in error.strip().splitlines() if ln.strip()]
    for prefix in ("! ", "error:", "fatal:", "remote:"):
        for ln in lines:
            if ln.startswith(prefix):
                return ln
    rest = [ln for ln in lines if not ln.startswith("To ")]
    return (rest or lines or ["unknown error"])[0]


def _out(git: Git, cwd: Path, args: list[str]) -> str | None:
    r = git(cwd, args)
    return r.out.strip() if r.code == 0 else None


class Publisher:
    """Publishes the snapshot for `serve`; `start_branch` and `start_commit` are the checkout's
    at start (the Python that is running)."""

    def __init__(
        self,
        paths: Paths,
        settings: PublishSettings,
        start_branch: str | None,
        git: Git = run_git,
        start_commit: str | None = None,
    ) -> None:
        self.paths, self.settings, self.git = paths, settings, git
        self.start_branch, self.start_commit = start_branch, start_commit

    def publish(self, now: datetime) -> Outcome:
        day = now.astimezone(MANILA).date().isoformat()
        try:
            return self._publish(now, day)
        except _Failed as e:
            return self._fail(day, str(e))
        except Exception as e:
            log.exception("publish: unexpected error")
            return self._fail(day, str(e) or type(e).__name__)

    def _fail(self, day: str, error: str) -> Outcome:
        log.warning("publish: failed: %s", error.strip())
        text = f"Site publish failed: {_first_line(error)}"
        return Outcome("failed", error, Note((f"publish:fail:{day}",), text))

    def _skip(self, day: str, reason: str, text: str) -> Outcome:
        log.info("publish: skipped: %s", text)
        return Outcome("skipped", text, Note((f"publish:skip:{reason}:{day}",), text))

    def _run(self, cwd: Path, args: list[str]) -> str:
        r = self.git(cwd, args)
        if r.code != 0:
            raise _Failed(r.err.strip() or r.out.strip() or f"git {args[0]} exited {r.code}")
        return r.out.strip()

    def _gate(self, day: str) -> Outcome | None:
        root = self.paths.root
        for branch in (
            self.start_branch,
            _out(self.git, root, ["rev-parse", "--abbrev-ref", "HEAD"]),
        ):
            if branch != MAIN:
                text = f"Site not published: serve is on {branch or 'an unknown branch'}, not main."
                return self._skip(day, "branch", text)
        if _out(self.git, root, ["rev-parse", "HEAD"]) != self.start_commit:
            text = "Site not published: main moved since serve started; restart serve."
            return self._skip(day, "moved", text)
        tracked = ["status", "--porcelain", "--untracked-files=no", "--", "src/outages"]
        static = ["status", "--porcelain", "--untracked-files=all", "--ignored", "--", STATIC_DIR]
        if self._run(root, tracked) or self._run(root, static):
            text = "Site not published: uncommitted changes in src/outages."
            return self._skip(day, "dirty", text)
        return None

    def _ours(self, site: Path, sha: str) -> bool:
        """Whether `sha` is a snapshot commit this publisher made: known locally, no parent,
        subject "snapshot: …" (review M2)."""
        info = _out(self.git, site, ["show", "-s", "--format=%P|%s", sha])
        return info is not None and info.startswith("|snapshot: ")

    def _restore(self, site: Path) -> None:
        """Put the working tree back to what is live, so the next write keeps its folders
        (review I1). Best effort."""
        for args in (["reset", "-q", "--hard", "HEAD"], ["clean", "-fdq"]):
            r = self.git(site, args)
            if r.code != 0:
                log.warning("publish: couldn't restore %s: %s", site, r.err.strip())

    def _publish(self, now: datetime, day: str) -> Outcome:
        skipped = self._gate(day)
        if skipped is not None:
            return skipped
        site = self.settings.site
        if (
            not site.is_dir()
            or _out(self.git, site, ["rev-parse", "--abbrev-ref", "HEAD"]) != SITE_BRANCH
        ):
            raise _Failed(f"{site} is not a git worktree on {SITE_BRANCH}")
        prev = self._run(site, ["rev-parse", "HEAD"])
        try:
            t = snapshot_run.take(self.paths, site, now)
            if t.code:
                raise _Failed(t.error or f"snapshot exited {t.code}")
            self._run(site, ["add", "-A"])
            tree = self._run(site, ["write-tree"])
            message = f"snapshot: {t.taken_at.isoformat()}"
            new = self._run(site, ["commit-tree", tree, "-m", message])
            remote = self._run(site, ["ls-remote", "origin", f"refs/heads/{SITE_BRANCH}"]).split()
            lease = prev
            if remote and remote[0] != prev and self._ours(site, remote[0]):
                lease = remote[0]  # our own earlier push landed; only the local step was lost
            push = ["push", f"--force-with-lease={SITE_BRANCH}:{lease}", "origin"]
            self._run(site, [*push, f"{new}:refs/heads/{SITE_BRANCH}"])
        except Exception:
            self._restore(site)
            raise
        self._run(site, ["reset", "--soft", new])
        log.info("publish: published %s (%s)", new[:7], message)
        return Outcome("published", f"{new[:7]} {message}", None)


def make_publisher(paths: Paths, git: Git = run_git) -> Publisher | None:
    """The publisher `serve` uses, or None when PUBLISH_SITE is off; records the branch and
    commit `serve` started on."""
    settings = publish_settings(paths.root)
    if settings is None:
        return None
    branch = _out(git, paths.root, ["rev-parse", "--abbrev-ref", "HEAD"])
    commit = _out(git, paths.root, ["rev-parse", "HEAD"])
    return Publisher(paths, settings, branch, git, start_commit=commit)
