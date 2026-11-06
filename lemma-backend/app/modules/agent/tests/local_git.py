"""Real git against a repository on local disk standing in for GitHub.

For the tests of what the sandbox runs on a conversation's repository: the
checkout that makes a worktree or a run's directory (``github_project``) and the sweep that clears
it (``worktree_sweep``). Both build a shell command; these run it, with the
clone and the worktrees laid out under a temporary root the way the sandbox
lays them out under ``~/lemma``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.modules.agent.infrastructure.tools.workspace_cli import (
    github_project,
    worktree_sweep,
)


class LocalGit:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.env = {
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Ada Member",
            "GIT_AUTHOR_EMAIL": "ada@example.test",
            "GIT_COMMITTER_NAME": "Ada Member",
            "GIT_COMMITTER_EMAIL": "ada@example.test",
        }
        self.origin = root / "origin.git"
        self.clone = root / "repos" / "acme" / "web"
        source = root / "source"
        self.run("init", "-q", "-b", "main", str(source))
        self.run("-C", str(source), "commit", "-q", "--allow-empty", "-m", "first")
        self.run("-C", str(source), "branch", "release")
        self.run("clone", "-q", "--bare", str(source), str(self.origin))

    def run(self, *args: str) -> str:
        done = subprocess.run(
            ["git", *args], env=self.env, capture_output=True, text=True, check=True
        )
        return done.stdout.strip()

    def push_commit(self, message: str) -> str:
        scratch = self.root / f"push-{message}"
        self.run("clone", "-q", str(self.origin), str(scratch))
        self.run("-C", str(scratch), "commit", "-q", "--allow-empty", "-m", message)
        self.run("-C", str(scratch), "push", "-q", "origin", "main")
        return self.run("-C", str(scratch), "rev-parse", "HEAD")

    def open_pull_request(self, number: int) -> str:
        """Push a commit only a pull request's head points at, as GitHub keeps
        one: under ``refs/pull``, which a plain clone does not fetch."""
        scratch = self.root / f"pull-{number}"
        self.run("clone", "-q", str(self.origin), str(scratch))
        self.run("-C", str(scratch), "commit", "-q", "--allow-empty", "-m", "pr")
        self.run(
            "-C", str(scratch), "push", "-q", "origin", f"HEAD:refs/pull/{number}/head"
        )
        return self.run("-C", str(scratch), "rev-parse", "HEAD")

    def worktree(self, slug: str) -> Path:
        return self.root / "worktrees" / "acme" / "web" / slug

    def step(self, slug: str, *, ref: str | None = None) -> tuple[int, Path]:
        """What the sandbox runs before a conversation's first command."""
        worktree = self.worktree(slug)
        # The session has already made its cwd by the time this runs.
        worktree.mkdir(parents=True, exist_ok=True)
        command = github_project._checkout_command(
            url=str(self.origin),
            clone_dir=str(self.clone),
            ref=ref,
            worktree=str(worktree),
            slug=slug,
        )
        return self._sh(command).returncode, worktree

    def run_step(self, name: str, *, ref: str | None = None) -> tuple[int, Path]:
        """What a shared pod machine runs before a run's first command."""
        run_dir = self.root / "runs" / "agent" / name
        run_dir.mkdir(parents=True, exist_ok=True)
        command = github_project._checkout_command(
            url=str(self.origin),
            clone_dir=str(self.clone),
            ref=ref,
            worktree=None,
            slug=None,
            run_dir=str(run_dir),
        )
        return self._sh(command).returncode, run_dir

    def sweep(self, *slugs: str) -> list[str]:
        """What the sandbox runs to clear finished worktrees; what it said."""
        command = worktree_sweep.sweep_command(
            [(str(self.clone), str(self.worktree(slug))) for slug in slugs]
        )
        done = self._sh(command)
        assert done.returncode == 0, done.stderr
        return done.stdout.splitlines()

    def _sh(self, command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", "-c", command], env=self.env, capture_output=True, text=True
        )


def local_git(root: Path) -> LocalGit:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    return LocalGit(root)
