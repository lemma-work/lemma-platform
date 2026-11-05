"""Clearing the worktrees a person's conversations are finished with.

Three layers, each tested as itself: the rule for "finished"
(``domain/worktree_sweep``), the shell command that removes a worktree, run
with real git against a local repository standing in for GitHub, and the step
that ties them to a sandbox -- with its Redis marker and its read of the
database answered by arguments, never by patching.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.project_repo import ProjectRepo, worktree_at
from app.modules.agent.domain.worktree_sweep import (
    ARCHIVED_QUIET,
    IDLE_QUIET,
    WorktreeUse,
    finished_worktrees,
)
from app.modules.agent.infrastructure.tools.workspace_cli import (
    github_project,
    worktree_sweep,
)
from app.modules.agent.tests.local_git import LocalGit, local_git
from sandbox_runtime.paths import WORKSPACE_ROOT

_NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
_WEB = ProjectRepo(owner="acme", repo="web")


def _use(
    slug: str,
    *,
    quiet: timedelta,
    archived: bool = False,
    running: bool = False,
    repo: ProjectRepo = _WEB,
) -> WorktreeUse:
    return WorktreeUse(
        cwd=repo.worktree_dir(slug),
        archived=archived,
        last_activity_at=_NOW - quiet,
        running=running,
    )


def _finished(*uses: WorktreeUse, keep: str | None = None) -> list[str]:
    return [
        repo.worktree_dir(slug)
        for repo, slug in finished_worktrees(uses, now=_NOW, keep=keep)
    ]


# --- the rule -----------------------------------------------------------------


def test_an_archived_conversation_lets_its_worktree_go_once_it_has_settled() -> None:
    settled = _use("one", quiet=ARCHIVED_QUIET, archived=True)
    just_archived = _use("two", quiet=timedelta(minutes=10), archived=True)

    assert _finished(settled, just_archived) == [_WEB.worktree_dir("one")]


def test_the_settling_hour_outlasts_the_checkout_marker() -> None:
    """Otherwise a conversation brought back from the archive could trust a
    marker saying its worktree is there, and start in an empty directory."""
    assert ARCHIVED_QUIET.total_seconds() > github_project._PRESENT_TTL_SECONDS


def test_a_conversation_nobody_archived_lets_go_after_two_weeks() -> None:
    left = _use("one", quiet=IDLE_QUIET)
    recent = _use("two", quiet=IDLE_QUIET - timedelta(hours=1))

    assert _finished(left, recent) == [_WEB.worktree_dir("one")]


def test_a_running_conversation_keeps_its_worktree_however_quiet() -> None:
    assert _finished(_use("one", quiet=timedelta(days=60), running=True)) == []
    assert (
        _finished(_use("one", quiet=timedelta(days=60), archived=True, running=True))
        == []
    )


def test_a_worktree_goes_only_when_everyone_working_in_it_is_finished() -> None:
    """A sub-agent works in its parent's worktree, so one row still going
    keeps it for all of them -- and once none is, it is removed once."""
    parent = _use("one", quiet=timedelta(days=1), archived=True)
    sub_agent = _use("one", quiet=timedelta(minutes=5))

    assert _finished(parent, sub_agent) == []
    assert _finished(parent, _use("one", quiet=timedelta(days=20))) == [
        _WEB.worktree_dir("one")
    ]


def test_the_conversation_asking_never_loses_its_own_worktree() -> None:
    mine = _use("one", quiet=timedelta(days=30), archived=True)

    assert _finished(mine, keep=_WEB.worktree_dir("one") + "/") == []


def test_only_worktrees_are_ever_swept() -> None:
    """The clone is every conversation's, and a scratchpad is not this rule's."""
    quiet = timedelta(days=30)
    others = [
        WorktreeUse(
            cwd=path, archived=True, last_activity_at=_NOW - quiet, running=False
        )
        for path in (
            _WEB.clone_dir,
            f"{WORKSPACE_ROOT}/c/2026-10-01/ab12cd34",
            f"{WORKSPACE_ROOT}/worktrees/acme/web",
            f"{WORKSPACE_ROOT}/worktrees/acme/web/ab12cd34/src",
            f"{WORKSPACE_ROOT}/worktrees/acme/web.git/ab12cd34",
            f"{WORKSPACE_ROOT}/worktrees/acme/web/AB12",
            f"{WORKSPACE_ROOT}/worktrees/acme/../ab12cd34",
        )
    ]

    assert _finished(*others) == []


def test_the_longest_quiet_go_first_and_a_sweep_is_bounded() -> None:
    uses = [_use(f"w{days}", quiet=timedelta(days=days)) for days in (15, 40, 20)]

    finished = finished_worktrees(uses, now=_NOW, limit=2)

    assert [slug for _, slug in finished] == ["w40", "w20"]


def test_a_worktree_path_reads_back_to_its_repository() -> None:
    repo = ProjectRepo(owner="Acme-Co", repo="web.app_2")

    assert worktree_at(repo.worktree_dir("ab12cd34")) == (repo, "ab12cd34")
    assert worktree_at(repo.clone_dir) is None


# --- the command, against real git ------------------------------------------


@pytest.fixture
def git(tmp_path: Path) -> LocalGit:
    return local_git(tmp_path)


def test_a_clean_worktree_goes_and_its_ignored_files_with_it(git: LocalGit) -> None:
    _, worktree = git.step("one")
    (worktree / ".gitignore").write_text("node_modules/\n")
    git.run("-C", str(worktree), "add", ".gitignore")
    git.run("-C", str(worktree), "commit", "-q", "-m", "ignore")
    (worktree / "node_modules").mkdir()
    (worktree / "node_modules" / "dep.js").write_text("installed")

    assert git.sweep("one") == [f"removed {worktree}"]
    assert not worktree.exists()


def test_uncommitted_work_keeps_its_worktree(git: LocalGit) -> None:
    _, edited = git.step("edited")
    _, untracked = git.step("untracked")
    (edited / ".keep").write_text("tracked")
    git.run("-C", str(edited), "add", ".keep")
    git.run("-C", str(edited), "commit", "-q", "-m", "keep")
    (edited / ".keep").write_text("changed, not committed")
    (untracked / "notes.md").write_text("never added")

    assert git.sweep("edited", "untracked") == [
        f"kept {edited}",
        f"kept {untracked}",
    ]
    assert (edited / ".keep").read_text() == "changed, not committed"
    assert (untracked / "notes.md").read_text() == "never added"


def test_a_swept_worktree_comes_back_on_its_branch_with_its_commits(
    git: LocalGit,
) -> None:
    """Commits that never reached GitHub are on the branch, which the sweep
    never deletes; the next checkout makes the worktree again on it."""
    _, worktree = git.step("one")
    git.run("-C", str(worktree), "commit", "-q", "--allow-empty", "-m", "mine")
    mine = git.run("-C", str(worktree), "rev-parse", "HEAD")

    assert git.sweep("one") == [f"removed {worktree}"]
    assert git.run("-C", str(git.clone), "rev-parse", "lemma/one") == mine

    code, worktree = git.step("one")

    assert code == 0
    assert git.run("-C", str(worktree), "rev-parse", "HEAD") == mine


def test_the_sweep_leaves_the_clone_and_every_other_worktree_alone(
    git: LocalGit,
) -> None:
    _, kept = git.step("kept")
    _, swept = git.step("swept")

    assert git.sweep("swept", "never-made") == [f"removed {swept}"]
    assert git.run("-C", str(kept), "branch", "--show-current") == "lemma/kept"
    assert git.run("-C", str(git.clone), "status", "--porcelain") == ""


# --- the step -----------------------------------------------------------------


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, tuple[str, int]] = {}

    async def set(self, key: str, value: str, *, ex: int, nx: bool = False):
        if nx and key in self.values:
            return None
        self.values[key] = (value, ex)
        return True


class _FakeSession:
    def __init__(self, result: dict | None = None) -> None:
        self.session_id = "session-1"
        self.calls: list[dict] = []
        self._result = result or {"success": True, "state": "running"}

    async def exec_command(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


def _context(cwd: str | None = None) -> ToolContext:
    return ToolContext(
        user_id=uuid4(),
        pod_id=uuid4(),
        conversation_id=uuid4(),
        workspace_repo=_WEB,
        workspace_cwd=cwd or _WEB.worktree_dir("mine"),
    )


class _Uses:
    def __init__(self, *uses: WorktreeUse) -> None:
        self.uses = list(uses)
        self.asked: list[tuple[UUID, str]] = []

    async def __call__(self, user_id: UUID, workspace_id: str) -> list[WorktreeUse]:
        self.asked.append((user_id, workspace_id))
        return self.uses


async def _sweep(ctx, session, redis, uses) -> None:
    await worktree_sweep.sweep_finished_worktrees(
        ctx, session, redis=redis, read_uses=uses, now=_NOW
    )


@pytest.mark.asyncio
async def test_the_sweep_starts_in_the_sandbox_and_is_left_to_run() -> None:
    ctx = _context()
    session = _FakeSession()
    uses = _Uses(
        _use("done", quiet=timedelta(days=20)),
        _use("mine", quiet=timedelta(days=20)),
    )

    await _sweep(ctx, session, _FakeRedis(), uses)

    assert uses.asked == [(ctx.user_id, "default")]
    [call] = session.calls
    assert f"worktree remove {_WEB.worktree_dir('done')}" in call["cmd"]
    # Its own directory is the one it is about to work in.
    assert _WEB.worktree_dir("mine") not in call["cmd"]
    # Handed over, not waited on: removing trees takes longer than an agent
    # should wait for its first command.
    assert call["yield_time_ms"] == 0
    assert call["timeout"] <= 5


@pytest.mark.asyncio
async def test_a_sandbox_is_swept_at_most_once_a_day() -> None:
    redis = _FakeRedis()
    ctx = _context()
    session = _FakeSession()
    uses = _Uses(_use("done", quiet=timedelta(days=20)))

    await _sweep(ctx, session, redis, uses)
    # Another conversation of the same person, same sandbox, same day.
    await _sweep(
        _context(_WEB.worktree_dir("other")).model_copy(
            update={"user_id": ctx.user_id}
        ),
        session,
        redis,
        uses,
    )

    assert len(uses.asked) == 1
    assert len(session.calls) == 1
    [(value, ttl)] = redis.values.values()
    assert ttl == 24 * 60 * 60


@pytest.mark.asyncio
async def test_nothing_finished_starts_nothing() -> None:
    session = _FakeSession()

    await _sweep(_context(), session, _FakeRedis(), _Uses(_use("x", quiet=timedelta())))

    assert session.calls == []


@pytest.mark.asyncio
async def test_a_sweep_that_fails_never_reaches_the_agent() -> None:
    async def _down(user_id, workspace_id):
        raise ConnectionError("database is down")

    session = _FakeSession()

    await _sweep(_context(), session, _FakeRedis(), _down)

    assert session.calls == []


@pytest.mark.asyncio
async def test_the_checkout_ends_on_the_sweep_only_when_it_ran() -> None:
    swept: list[str] = []

    async def _sweep_step(ctx, session):
        swept.append(ctx.get_workspace_cwd())

    class _Redis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        async def exists(self, key):
            return key in self.values

        async def set(self, key, value, *, ex):
            self.values[key] = value

    redis = _Redis()
    session = _FakeSession({"success": True, "exit_code": 0, "stderr": ""})
    ctx = _context()

    await github_project.ensure_project_checkout(
        ctx, session, redis=redis, sweep=_sweep_step
    )
    await github_project.ensure_project_checkout(
        ctx, session, redis=redis, sweep=_sweep_step
    )

    # The second command found the checkout marked present and did nothing.
    assert swept == [_WEB.worktree_dir("mine")]
