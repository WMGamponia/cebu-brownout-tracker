"""The live site (live-site spec 5.2, 5.3): after a successful check, `serve` builds the snapshot
into the gh-pages worktree and replaces gh-pages with one parentless commit, force-pushed with a
lease. Only from `main`, with no tracked changes under src/outages. Never raises: the outcome
(and at most one admin note per Manila day and reason) goes back to the run."""

from __future__ import annotations

import logging
import os
import subprocess
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
GIT_TIMEOUT_S = 120


@dataclass(frozen=True)
class GitResult:
    code: int
    out: str
    err: str


Git = Callable[[Path, list[str]], GitResult]


def run_git(cwd: Path, args: list[str]) -> GitResult:
    """`git <args>` in `cwd`; never prompts for credentials, gives up after GIT_TIMEOUT_S."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            env=env,
            timeout=GIT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return GitResult(124, "", f"timed out after {GIT_TIMEOUT_S} s")
    except OSError as e:
        return GitResult(127, "", str(e))
    return GitResult(r.returncode, r.stdout, r.stderr)


@dataclass(frozen=True)
class Outcome:
    status: Literal["published", "skipped", "failed"]
    detail: str
    note: Note | None


class _Failed(Exception):
    pass


def _branch(git: Git, cwd: Path) -> str | None:
    r = git(cwd, ["rev-parse", "--abbrev-ref", "HEAD"])
    return r.out.strip() if r.code == 0 else None


class Publisher:
    """Publishes the snapshot for `serve`; `start_branch` is the checkout's branch at start."""

    def __init__(
        self,
        paths: Paths,
        settings: PublishSettings,
        start_branch: str | None,
        git: Git = run_git,
    ) -> None:
        self.paths, self.settings, self.start_branch, self.git = paths, settings, start_branch, git

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
        first = (error.strip().splitlines() or ["unknown error"])[0]
        log.warning("publish: failed: %s", error.strip())
        return Outcome(
            "failed", error, Note((f"publish:fail:{day}",), f"Site publish failed: {first}")
        )

    def _skip(self, day: str, reason: str, text: str) -> Outcome:
        log.info("publish: skipped: %s", text)
        return Outcome("skipped", text, Note((f"publish:skip:{reason}:{day}",), text))

    def _run(self, cwd: Path, args: list[str]) -> str:
        r = self.git(cwd, args)
        if r.code != 0:
            raise _Failed(r.err.strip() or r.out.strip() or f"git {args[0]} exited {r.code}")
        return r.out.strip()

    def _publish(self, now: datetime, day: str) -> Outcome:
        root, site = self.paths.root, self.settings.site
        for branch in (self.start_branch, _branch(self.git, root)):
            if branch != MAIN:
                text = f"Site not published: serve is on {branch or 'an unknown branch'}, not main."
                return self._skip(day, "branch", text)
        status = ["status", "--porcelain", "--untracked-files=no", "--", "src/outages"]
        if self._run(root, status):
            return self._skip(
                day, "dirty", "Site not published: uncommitted changes in src/outages."
            )
        if not site.is_dir() or _branch(self.git, site) != SITE_BRANCH:
            raise _Failed(f"{site} is not a git worktree on {SITE_BRANCH}")
        prev = self._run(site, ["rev-parse", "HEAD"])
        t = snapshot_run.take(self.paths, site, now)
        if t.code:
            raise _Failed(t.error or f"snapshot exited {t.code}")
        self._run(site, ["add", "-A"])
        tree = self._run(site, ["write-tree"])
        message = f"snapshot: {t.taken_at.isoformat()}"
        new = self._run(site, ["commit-tree", tree, "-m", message])
        lease = f"--force-with-lease={SITE_BRANCH}:{prev}"
        self._run(site, ["push", lease, "origin", f"{new}:refs/heads/{SITE_BRANCH}"])
        self._run(site, ["reset", "--soft", new])
        log.info("publish: published %s (%s)", new[:7], message)
        return Outcome("published", f"{new[:7]} {message}", None)


def make_publisher(paths: Paths, git: Git = run_git) -> Publisher | None:
    """The publisher `serve` uses, or None when PUBLISH_SITE is off; records the start branch."""
    settings = publish_settings(paths.root)
    if settings is None:
        return None
    return Publisher(paths, settings, _branch(git, paths.root), git)
