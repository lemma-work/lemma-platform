from __future__ import annotations

import shutil
from functools import partial
from pathlib import Path
from sandbox_runtime.paths import RUNS_ROOT, WORKSPACE_ROOT

from uuid import uuid4

import pytest

from app.modules.agent.domain.workspace_location import ProjectRepo
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.workspace_cli import (
    github_project,
    workspace_cli,
)
from app.modules.agent.infrastructure.tools.workspace_cli.models import (
    ExecCommandRequest,
    ExecutePythonRequest,
)
from app.modules.agent.tests.local_git import LocalGit, local_git
from app.modules.workspace.contracts import PythonExecutionResult


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def exists(self, key: str) -> int:
        return 1 if key in self.values else 0

    async def set(self, key: str, value: str, *, ex: int):
        del ex
        self.values[key] = value


class _FakeSession:
    def __init__(self, *, exit_code: int = 0, stderr: str = "") -> None:
        self.session_id = "session-1"
        self.cwd: str | None = None
        self.commands: list[str] = []
        self._exit_code = exit_code
        self._stderr = stderr

    async def exec_command(self, *, cmd: str, timeout: int | None = None):
        del timeout
        self.commands.append(cmd)
        return {
            "success": self._exit_code == 0,
            "exit_code": self._exit_code,
            "stderr": self._stderr,
        }


def _context(repo: ProjectRepo | None) -> ToolContext:
    return ToolContext(
        user_id=uuid4(),
        pod_id=uuid4(),
        conversation_id=uuid4(),
        workspace_repo=repo,
    )


async def _no_sweep(ctx, session) -> None:
    del ctx, session


#: The checkout step without the sweep it ends on, which reads the database
#: and is `test_worktree_sweep`'s to test.
_ensure = partial(github_project.ensure_project_checkout, sweep=_no_sweep)

_REPO = ProjectRepo(owner="acme", repo="web")
_MARKER_KEY = f"{github_project._MARKER_KEY_PREFIX}:session-1:acme/web"


@pytest.mark.asyncio
async def test_a_conversation_without_a_project_does_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(url=None):
        raise AssertionError("must not touch Redis for a scratchpad conversation")

    monkeypatch.setattr(github_project, "get_redis", _fail)
    session = _FakeSession()

    assert await _ensure(_context(None), session) is None
    assert session.commands == []


@pytest.mark.asyncio
async def test_clone_runs_once_per_session(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()
    monkeypatch.setattr(github_project, "get_redis", lambda url=None: redis)
    ctx = _context(_REPO)
    session = _FakeSession()

    assert await _ensure(ctx, session) is None
    assert redis.values[_MARKER_KEY] == "present"

    # Twenty commands into a session, the clone is not re-attempted.
    assert await _ensure(ctx, session) is None
    assert len(session.commands) == 1


@pytest.mark.asyncio
async def test_clone_never_runs_over_an_existing_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard is in the command itself, so a fresh session cannot clobber a
    working tree another conversation is using: an existing checkout is only
    fetched, which moves remote-tracking refs and nothing else."""

    redis = _FakeRedis()
    monkeypatch.setattr(github_project, "get_redis", lambda url=None: redis)
    session = _FakeSession()

    await _ensure(_context(_REPO), session)

    command = session.commands[0]
    path = f"{WORKSPACE_ROOT}/repos/acme/web"
    # A conversation from before worktrees works in the clone itself, so it is
    # cloned or fetched there and nothing else is made.
    assert command.startswith(
        f"if [ -e {path}/.git ]; then git -C {path} fetch --quiet origin || stale=1; "
        f"else git clone https://github.com/acme/web.git {path} || exit; fi"
    )
    assert "worktree" not in command
    # Nothing that could move or discard work in an existing tree.
    for destructive in ("pull", "reset", "checkout", "switch", "clean", "merge"):
        assert destructive not in command


@pytest.mark.asyncio
async def test_a_ref_is_cloned_as_a_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()
    monkeypatch.setattr(github_project, "get_redis", lambda url=None: redis)
    repo = ProjectRepo(owner="acme", repo="web", ref="release/2.0")
    session = _FakeSession()

    await _ensure(_context(repo), session)

    # No quoting appears because nothing needs it: a ref that reached this far
    # already passed a charset with no shell metacharacters in it. The
    # `shlex.quote` in the builder is the second line of defence, not the first.
    assert "git clone --branch release/2.0 " in session.commands[0]


@pytest.mark.asyncio
async def test_a_failed_clone_tells_the_agent_why_the_directory_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _FakeRedis()
    monkeypatch.setattr(github_project, "get_redis", lambda url=None: redis)
    session = _FakeSession(
        exit_code=128, stderr="remote: Repository not found.\nfatal: could not read"
    )

    notice = await _ensure(_context(_REPO), session)

    assert notice is not None
    assert "acme/web" in notice
    assert f"{WORKSPACE_ROOT}/repos/acme/web" in notice
    # git's own words survive: they distinguish a missing repo from no access.
    assert "Repository not found." in notice
    # Cached, so a burst of commands doesn't re-run a slow failing clone...
    assert redis.values[_MARKER_KEY] == "failed"
    assert await _ensure(_context(_REPO), session) is None
    assert len(session.commands) == 1


@pytest.mark.asyncio
async def test_a_failed_fetch_says_origin_may_be_stale_once() -> None:
    """The checkout is still there and usable, so it is not a failed clone; the
    agent is told once that `origin/*` may be behind."""
    redis = _FakeRedis()
    session = _FakeSession(
        exit_code=75,
        stderr="fatal: unable to access 'https://github.com/acme/web.git/'",
    )

    notice = await _ensure(_context(_REPO), session, redis=redis)

    assert notice is not None
    assert "is checked out in" in notice
    assert "`origin/*` may be behind GitHub" in notice
    assert "unable to access" in notice
    assert "could not be cloned" not in notice
    assert redis.values[_MARKER_KEY] == "present"
    assert await _ensure(_context(_REPO), session, redis=redis) is None


@pytest.mark.asyncio
async def test_a_session_without_an_id_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _FakeRedis()
    monkeypatch.setattr(github_project, "get_redis", lambda url=None: redis)
    session = _FakeSession()
    session.session_id = None

    assert await _ensure(_context(_REPO), session) is None
    assert session.commands == []


# --- the workspace_cli hook --------------------------------------------------


class _RecordingWorkspaceSession:
    def __init__(self) -> None:
        self.session_id = "session-1"
        self.auto_close = False
        self.workspace_recreated = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb

    async def exec_command(self, **kwargs):
        del kwargs
        return {
            "success": True,
            "stdout": "the command ran",
            "stderr": "",
            "exit_code": 0,
            "completed": True,
            "process_id": None,
        }

    async def execute_code(self, code, timeout_seconds=60):
        del code, timeout_seconds
        return PythonExecutionResult(
            success=True,
            stdout="the code ran",
            stderr="",
            result=None,
            error_in_exec=None,
        )


class _RecordingRuntime:
    def __init__(self, session):
        self._session = session

    async def get_session(self, **kwargs):
        del kwargs
        return self._session

    async def bind_process_to_session(self, **kwargs):
        del kwargs


def _patch_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workspace_cli,
        "get_workspace_tool_runtime",
        lambda: _RecordingRuntime(_RecordingWorkspaceSession()),
    )


# Both workspace tools run in the conversation's one directory, so both have to
# find the same thing in it. These are parametrized over the two rather than
# written for the shell alone, which is how `execute_python` came to be the one
# tool that opened a project and found an empty folder.
_TOOLS = ("exec_command", "execute_python")


async def _run_tool(name: str, ctx):
    if name == "exec_command":
        return await workspace_cli.exec_command_internal(
            ctx, ExecCommandRequest(cmd="pwd")
        )
    return await workspace_cli.execute_python_internal(
        ctx, ExecutePythonRequest(code="print(1)")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TOOLS)
async def test_a_project_conversation_checks_out_before_any_tool_call(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not just git-looking commands, and not just the shell: the project has to
    be on disk before `ls`, and before the first line of Python."""

    _patch_runtime(monkeypatch)
    calls: list[str] = []

    async def _creds(ctx, session):
        del ctx, session
        calls.append("credentials")

    async def _checkout(ctx, session):
        del ctx, session
        calls.append("checkout")
        return

    monkeypatch.setattr(github_project, "ensure_github_credentials", _creds)
    monkeypatch.setattr(github_project, "ensure_project_checkout", _checkout)

    await _run_tool(tool, _context(_REPO))

    # Credentials first: a private repo cannot be cloned without them.
    assert calls == ["credentials", "checkout"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TOOLS)
async def test_a_scratchpad_conversation_is_untouched_by_the_project_step(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runtime(monkeypatch)

    async def _fail(ctx, session):
        raise AssertionError("no project, nothing to check out")

    monkeypatch.setattr(github_project, "ensure_project_checkout", _fail)

    result = await _run_tool(tool, _context(None))

    assert result.success is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool, ran",
    [("exec_command", "the command ran"), ("execute_python", "the code ran")],
)
async def test_a_failed_checkout_reaches_the_agent_without_failing_the_work(
    tool: str, ran: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notice is the whole point: an empty directory with no explanation is
    what the agent is left to guess at, whichever tool it reached for."""

    _patch_runtime(monkeypatch)

    async def _creds(ctx, session):
        del ctx, session

    async def _checkout(ctx, session):
        del ctx, session
        return "[workspace notice] acme/web could not be cloned"

    monkeypatch.setattr(github_project, "ensure_github_credentials", _creds)
    monkeypatch.setattr(github_project, "ensure_project_checkout", _checkout)

    result = await _run_tool(tool, _context(_REPO))

    assert result.success is True
    assert result.stdout is not None
    assert result.stdout.startswith("[workspace notice] acme/web could not be cloned")
    # The tool's own output is still there, under the notice.
    assert ran in result.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TOOLS)
async def test_a_broken_bridge_never_blocks_the_work_itself(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Redis or DB hiccup must not turn into a failed tool call: the work runs
    uncredentialed and fails with git's own error, exactly as with no bridge."""

    _patch_runtime(monkeypatch)

    async def _broken(ctx, session):
        raise RuntimeError("redis is down")

    monkeypatch.setattr(github_project, "ensure_github_credentials", _broken)

    result = await _run_tool(tool, _context(_REPO))

    assert result.success is True


# --- worktrees -----------------------------------------------------------------


def _worktree_context(slug: str = "ab12cd34", repo: ProjectRepo = _REPO) -> ToolContext:
    return ToolContext(
        user_id=uuid4(),
        pod_id=uuid4(),
        conversation_id=uuid4(),
        workspace_repo=repo,
        workspace_cwd=repo.worktree_dir(slug),
    )


@pytest.mark.asyncio
async def test_a_conversation_gets_a_worktree_of_the_shared_clone() -> None:
    session = _FakeSession()

    notice = await _ensure(_worktree_context(), session, redis=_FakeRedis())

    assert notice is None
    command = session.commands[0]
    clone = f"{WORKSPACE_ROOT}/repos/acme/web"
    worktree = f"{WORKSPACE_ROOT}/worktrees/acme/web/ab12cd34"
    assert f"git clone https://github.com/acme/web.git {clone}" in command
    assert (
        f'git -C {clone} worktree add --quiet -b lemma/ab12cd34 {worktree} "$start"'
        in command
    )


@pytest.mark.asyncio
async def test_a_worktree_that_could_not_be_made_says_so() -> None:
    redis = _FakeRedis()
    session = _FakeSession(
        exit_code=76, stderr="fatal: invalid reference: origin/release"
    )

    notice = await _ensure(_worktree_context(), session, redis=redis)

    assert notice is not None
    assert "worktree could not be made" in notice
    assert f"{WORKSPACE_ROOT}/worktrees/acme/web/ab12cd34" in notice
    assert "invalid reference" in notice
    assert redis.values[_MARKER_KEY] == "failed"


@pytest.fixture
def git(tmp_path: Path) -> LocalGit:
    return local_git(tmp_path)


def test_two_conversations_never_share_a_working_tree(git: LocalGit) -> None:
    first_code, first = git.step("one")
    second_code, second = git.step("two")

    assert (first_code, second_code) == (0, 0)
    assert git.run("-C", str(first), "branch", "--show-current") == "lemma/one"
    assert git.run("-C", str(second), "branch", "--show-current") == "lemma/two"
    (first / "draft.txt").write_text("unsaved")
    assert git.run("-C", str(second), "status", "--porcelain") == ""
    # One clone behind both, and nobody works in it.
    assert git.run("-C", str(git.clone), "status", "--porcelain") == ""


def test_a_new_conversation_starts_from_where_origin_is_now(git: LocalGit) -> None:
    git.step("one")
    latest = git.push_commit("second")

    code, worktree = git.step("two")

    assert code == 0
    assert git.run("-C", str(worktree), "rev-parse", "HEAD") == latest


def test_a_conversation_on_a_ref_starts_from_that_branch(git: LocalGit) -> None:
    code, worktree = git.step("one", ref="release")

    assert code == 0
    assert git.run("-C", str(worktree), "rev-parse", "HEAD") == git.run(
        "-C", str(git.clone), "rev-parse", "origin/release"
    )


def test_a_lost_worktree_comes_back_on_its_branch_with_its_commits(
    git: LocalGit,
) -> None:
    _, worktree = git.step("one")
    git.run("-C", str(worktree), "commit", "-q", "--allow-empty", "-m", "mine")
    mine = git.run("-C", str(worktree), "rev-parse", "HEAD")
    shutil.rmtree(worktree)

    code, worktree = git.step("one")

    assert code == 0
    assert git.run("-C", str(worktree), "rev-parse", "HEAD") == mine


def test_an_unreachable_origin_still_leaves_a_worktree_and_says_fetch_failed(
    git: LocalGit,
) -> None:
    git.step("one")
    git.origin.rename(git.root / "gone.git")

    code, worktree = git.step("two")

    assert code == github_project._FETCH_FAILED_EXIT
    assert git.run("-C", str(worktree), "branch", "--show-current") == "lemma/two"


@pytest.mark.asyncio
async def test_a_run_on_a_shared_machine_checks_out_into_its_own_directory() -> None:
    """The session already starts in the run's directory; that is where the
    project goes, detached, and the person's worktree sweep is not run there."""
    session = _FakeSession()
    session.cwd = f"{RUNS_ROOT}/reviewer/run-1"
    swept: list[object] = []

    async def sweep(ctx, workspace_session) -> None:
        swept.append(workspace_session)

    notice = await github_project.ensure_project_checkout(
        _worktree_context(), session, redis=_FakeRedis(), sweep=sweep
    )

    assert notice is None
    command = session.commands[0]
    clone = f"{WORKSPACE_ROOT}/repos/acme/web"
    run_dir = f"{RUNS_ROOT}/reviewer/run-1"
    assert f'git -C {clone} worktree add --quiet --detach {run_dir} "$start"' in command
    assert "lemma/ab12cd34" not in command
    assert swept == []


def test_a_run_directory_is_a_detached_worktree_of_the_shared_clone(
    git: LocalGit,
) -> None:
    first_code, first = git.run_step("one")
    second_code, second = git.run_step("two")

    assert (first_code, second_code) == (0, 0)
    assert git.run("-C", str(first), "branch", "--show-current") == ""
    (first / "draft.txt").write_text("unsaved")
    assert git.run("-C", str(second), "status", "--porcelain") == ""
    # A run directory the machine cleared does not stop the next one.
    shutil.rmtree(first)
    code, _ = git.run_step("three")
    assert code == 0


def test_a_pull_request_is_checked_out_by_its_own_head(git: LocalGit) -> None:
    head = git.open_pull_request(12)

    code, worktree = git.step("one", ref="pull/12/head")
    run_code, run_dir = git.run_step("one", ref="pull/12/head")

    assert (code, run_code) == (0, 0)
    assert git.run("-C", str(worktree), "rev-parse", "HEAD") == head
    assert git.run("-C", str(run_dir), "rev-parse", "HEAD") == head
