from __future__ import annotations

from contextlib import asynccontextmanager
from functools import partial
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.authorization.current import get_current_context
from app.modules.agent.services.workspace_location import ProjectRepo
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli import github_credential_bridge as bridge
from app.modules.agent.tools.workspace_cli import github_project
from app.modules.agent.tools.workspace_cli import workspace_cli
from app.modules.agent.tools.workspace_cli.models import ExecCommandRequest
from app.modules.connectors.domain.errors import (
    AccountResolutionError,
    ConnectorAccessDeniedError,
)

# Nothing in this file is patched. `ensure_github_credentials` takes its Redis
# client and its credential resolver as arguments, `_resolve_github_credential`
# takes its unit of work and its two connector collaborators, and
# `prepare_project_directory` takes the two steps it sequences — so every test
# below runs the bridge's own decisions (the marker key, the TTLs, which
# outcome is cacheable, the file contents, the shell command) rather than a
# stand-in for them.


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.calls: list[tuple] = []

    async def exists(self, key: str) -> int:
        self.calls.append(("exists", key))
        return 1 if key in self.values else 0

    async def set(self, key: str, value: str, *, ex: int):
        self.calls.append(("set", key, value, ex))
        self.values[key] = value


class _FakeCredentialSession:
    def __init__(self, session_id: str | None = "session-1"):
        self.session_id = session_id
        self.written: list[tuple[str, bytes]] = []
        self.commands: list[str] = []

    async def write_file(self, path: str, data: bytes):
        self.written.append((path, data))

    async def exec_command(self, *, cmd: str, timeout: int | None = None):
        del timeout
        self.commands.append(cmd)
        return {"success": True}


def _context() -> BaseAgentContext:
    return BaseAgentContext(user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4())


def _credential(**overrides):
    """A resolver returning one credential, recording who asked."""
    calls: list[BaseAgentContext] = []

    async def _resolve(ctx):
        calls.append(ctx)
        return bridge._GithubCredential(
            **{
                "access_token": "gho_faketoken123",
                "login": "octocat",
                "email": "octocat@example.com",
                **overrides,
            }
        )

    _resolve.calls = calls  # type: ignore[attr-defined]
    return _resolve


def _resolves_to_nothing():
    calls: list[BaseAgentContext] = []

    async def _resolve(ctx):
        calls.append(ctx)
        return

    _resolve.calls = calls  # type: ignore[attr-defined]
    return _resolve


def _must_not_resolve():
    async def _resolve(ctx):
        raise AssertionError("must not resolve a credential on this path")

    return _resolve


# The account is part of the key so two conversations in one session cannot
# inherit each other's credential file; "default" is the no-account-named case.
_MARKER_KEY = f"{bridge._MARKER_KEY_PREFIX}:session-1:default"


@pytest.mark.parametrize(
    "cmd, expected",
    [
        ("git clone https://github.com/foo/bar", True),
        ("echo hi && git push", True),
        ("gh pr list", True),
        ("  git status", True),
        ("cd /workspace/repo; git log", True),
        ("echo git", False),
        ("npm install", False),
        ('echo "gitfoo"', False),
        ("cargo build", False),
    ],
)
def test_looks_like_git_command(cmd: str, expected: bool) -> None:
    assert bridge.looks_like_git_command(cmd) is expected


@pytest.mark.asyncio
async def test_ensure_github_credentials_skips_when_already_provisioned() -> None:
    redis = _FakeRedis()
    redis.values[_MARKER_KEY] = "provisioned"

    session = _FakeCredentialSession()
    await bridge.ensure_github_credentials(
        _context(),
        session,
        redis=redis,
        resolve_credential=_must_not_resolve(),
    )

    assert session.written == []
    assert session.commands == []


@pytest.mark.asyncio
async def test_ensure_github_credentials_caches_no_account_as_unavailable() -> None:
    redis = _FakeRedis()

    session = _FakeCredentialSession()
    await bridge.ensure_github_credentials(
        _context(), session, redis=redis, resolve_credential=_resolves_to_nothing()
    )

    assert session.written == []
    assert session.commands == []
    assert redis.values[_MARKER_KEY] == "unavailable"
    # Short, so connecting the right account and carrying on works without
    # waiting out the provisioned TTL.
    assert ("set", _MARKER_KEY, "unavailable", bridge._UNAVAILABLE_TTL_SECONDS) in (
        redis.calls
    )


@pytest.mark.asyncio
async def test_ensure_github_credentials_writes_credential_file_and_marks_provisioned() -> (
    None
):
    redis = _FakeRedis()

    session = _FakeCredentialSession()
    await bridge.ensure_github_credentials(
        _context(), session, redis=redis, resolve_credential=_credential()
    )

    written = dict(session.written)
    assert written["/tmp/.git-credentials"] == (
        b"https://x-access-token:gho_faketoken123@github.com\n"
    )
    # `gh` does not read git's credential file. Writing its own config is what
    # makes `gh` authenticate as the same account, without the token entering
    # the environment where `env` would print it into a tool result.
    assert b"oauth_token: gho_faketoken123" in written["/tmp/lemma-gh/hosts.yml"]
    assert b"user: octocat" in written["/tmp/lemma-gh/hosts.yml"]

    assert len(session.commands) == 1
    cmd = session.commands[0]
    assert "credential.helper" in cmd
    assert "/tmp/.git-credentials" in cmd
    assert "user.name octocat" in cmd
    assert "user.email octocat@example.com" in cmd
    # Both credential files are delivered via write_file, never interpolated
    # into a shell string that could end up in process listings or command
    # logs. The permissions are what the command is for.
    assert "gho_faketoken123" not in cmd
    assert "chmod 600 /tmp/lemma-gh/hosts.yml" in cmd
    assert redis.values[_MARKER_KEY] == "provisioned"
    assert ("set", _MARKER_KEY, "provisioned", bridge._PROVISIONED_TTL_SECONDS) in (
        redis.calls
    )


@pytest.mark.asyncio
async def test_ensure_github_credentials_keys_the_marker_by_account() -> None:
    """A conversation bound to a project names the account it works as. Two
    conversations sharing one session must not inherit each other's file."""
    redis = _FakeRedis()
    account_id = uuid4()
    ctx = _context()
    ctx.workspace_repo = ProjectRepo(
        owner="acme", repo="widgets", account_id=account_id
    )

    await bridge.ensure_github_credentials(
        ctx,
        _FakeCredentialSession(),
        redis=redis,
        resolve_credential=_credential(),
    )

    assert f"{bridge._MARKER_KEY_PREFIX}:session-1:{account_id}" in redis.values, (
        redis.values
    )
    assert _MARKER_KEY not in redis.values


@pytest.mark.asyncio
async def test_ensure_github_credentials_falls_back_to_noreply_email() -> None:
    """A private-email account (GitHub's "keep my email address private")
    still needs a git-committable email -- fall back to GitHub's own noreply
    convention rather than leaving user.email unset."""
    session = _FakeCredentialSession()
    await bridge.ensure_github_credentials(
        _context(),
        session,
        redis=_FakeRedis(),
        resolve_credential=_credential(email=None),
    )

    cmd = session.commands[0]
    assert "user.name octocat" in cmd
    assert "user.email octocat@users.noreply.github.com" in cmd


@pytest.mark.asyncio
async def test_ensure_github_credentials_skips_identity_setup_without_login() -> None:
    """An account connected before profile enrichment existed has no login on
    file -- the credential file still gets written (git/gh still work), it
    just can't configure a commit identity for itself."""
    session = _FakeCredentialSession()
    await bridge.ensure_github_credentials(
        _context(),
        session,
        redis=_FakeRedis(),
        resolve_credential=_credential(login=None, email=None),
    )

    cmd = session.commands[0]
    assert "user.name" not in cmd
    assert "user.email" not in cmd


@pytest.mark.asyncio
async def test_ensure_github_credentials_noop_without_session_id() -> None:
    """No session, no credential file — and nothing asked of Redis or the
    connector layer on the way to deciding that."""
    redis = _FakeRedis()
    resolve = _must_not_resolve()

    session = _FakeCredentialSession(session_id=None)
    await bridge.ensure_github_credentials(
        _context(), session, redis=redis, resolve_credential=resolve
    )

    assert redis.calls == []
    assert session.written == []


class _RecordingWorkspaceSession:
    """Minimal exec-capable session for exercising the workspace_cli hook."""

    def __init__(self, session_id: str = "session-1"):
        self.session_id = session_id
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
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "completed": True,
            "process_id": None,
        }


class _RecordingRuntime:
    def __init__(self, session):
        self._session = session

    async def get_session(self, **kwargs):
        del kwargs
        return self._session

    async def bind_process_to_session(self, **kwargs):
        del kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cmd, repo, wanted",
    [
        ("pwd", None, False),
        ("git status", None, True),
        # A repo-backed conversation needs credentials for every command, not
        # just git-looking ones: the clone that puts the project on disk has to
        # happen before whatever the agent actually asked for, even `ls`.
        ("ls", ProjectRepo(owner="acme", repo="widgets"), True),
    ],
)
async def test_exec_command_internal_asks_for_project_preparation_when_wanted(
    cmd: str, repo: ProjectRepo | None, wanted: bool
) -> None:
    """The gate itself, rather than what the bridge did behind it.

    This used to replace `ensure_github_credentials` and assert it was called,
    which conflated the caller's `wanted=` decision with the callee's own
    early return — so the two could disagree and this file could not tell.
    """
    asked: list[bool] = []

    async def _prepare(ctx, workspace_session, *, wanted):
        del ctx, workspace_session
        asked.append(wanted)
        return

    ctx = _context()
    if repo is not None:
        ctx.workspace_repo = repo

    await workspace_cli.exec_command_internal(
        ctx,
        ExecCommandRequest(cmd=cmd),
        runtime=_RecordingRuntime(_RecordingWorkspaceSession()),
        prepare_project=_prepare,
    )

    assert asked == [wanted]


@pytest.mark.asyncio
async def test_exec_command_internal_runs_command_even_if_bridge_raises() -> None:
    """A broken credential bridge must not fail the underlying command.

    The swallow lives in `prepare_project_directory`, and that is the code
    running here: only the credential step itself is replaced, so the
    exception really does travel out of it, through the real handler, and back
    into `exec_command_internal`.
    """

    async def _broken_bridge(ctx, workspace_session):
        del ctx, workspace_session
        raise RuntimeError("redis is down")

    result = await workspace_cli.exec_command_internal(
        _context(),
        ExecCommandRequest(cmd="git push"),
        runtime=_RecordingRuntime(_RecordingWorkspaceSession()),
        prepare_project=partial(
            github_project.prepare_project_directory,
            ensure_credentials=_broken_bridge,
        ),
    )

    # It should still run (and, without credentials, fail with its own
    # native git auth error, not a generic workspace-tool error).
    assert result.success is True
    assert result.error is None


# --------------------------------------------------------------------------
# `_resolve_github_credential` itself. Every test above supplies a resolver, so
# none of them reach its own account-resolution/error handling. It takes its
# unit of work as an argument now, so these no longer open a real async session
# and rely on nobody executing a query inside it.


def _project_context(*, account_id: UUID | None = None) -> BaseAgentContext:
    ctx = _context()
    if account_id is not None:
        ctx.workspace_repo = ProjectRepo(
            owner="acme", repo="widgets", account_id=account_id
        )
    return ctx


_DELEGATED = SimpleNamespace(organization_id=None)


async def _fake_build_delegated_context(uow, ctx):
    del uow, ctx
    return _DELEGATED


def _uow_factory():
    @asynccontextmanager
    async def _scope():
        yield SimpleNamespace(session=None)

    return lambda: _scope()


async def _resolve(ctx, service):
    return await bridge._resolve_github_credential(
        ctx,
        uow_factory=_uow_factory(),
        delegated_context=_fake_build_delegated_context,
        account_resolution=lambda _uow: service,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        AccountResolutionError("no connected account"),
        ConnectorAccessDeniedError("not authorized"),
    ],
)
async def test_resolve_github_credential_returns_none_when_resolution_is_denied(
    error: Exception,
) -> None:
    """Neither "no account connected" nor "not authorized" is an error the
    caller should see -- both mean the same thing to a git command: there is
    no credential to provision, so `ensure_github_credentials` caches
    "unavailable" and lets the underlying command run without one."""

    class _DenyingResolution:
        async def resolve_account(self, **kwargs):
            del kwargs
            raise error

    assert await _resolve(_context(), _DenyingResolution()) is None


@pytest.mark.asyncio
async def test_resolve_github_credential_returns_none_without_an_access_token() -> None:
    class _NoTokenResolution:
        async def resolve_account(self, **kwargs):
            del kwargs
            return SimpleNamespace(
                credentials=SimpleNamespace(),  # no access_token at all
                display_name="octocat",
                email="octocat@example.com",
            )

    assert await _resolve(_context(), _NoTokenResolution()) is None


class _WorkingResolution:
    def __init__(self, *, email: str | None = "octocat@example.com"):
        self.captured: dict[str, object] = {}
        self.context_while_resolving: object = "not-set"
        self._email = email

    async def resolve_account(self, **kwargs):
        self.captured.update(kwargs)
        self.context_while_resolving = get_current_context()
        return SimpleNamespace(
            credentials=SimpleNamespace(access_token="gho_realtoken"),
            display_name="octocat",
            email=self._email,
        )


@pytest.mark.asyncio
async def test_resolve_github_credential_returns_a_credential_on_success() -> None:
    resolution = _WorkingResolution()
    ctx = _context()

    credential = await _resolve(ctx, resolution)

    assert credential == bridge._GithubCredential(
        access_token="gho_realtoken", login="octocat", email="octocat@example.com"
    )
    assert resolution.captured["user_id"] == ctx.user_id
    assert resolution.captured["connector_id"] == "github"
    # No project repo, so no account is named -- resolution falls back to
    # picking for the user (ambiguous only if they connected GitHub twice).
    assert resolution.captured["account_id"] is None


@pytest.mark.asyncio
async def test_resolve_github_credential_resolves_under_the_delegated_context() -> None:
    """The connector layer authorizes off the *current* context, so the
    delegated one has to be installed for the call and taken back down after
    it. Neither half was observable while this function built its own unit of
    work: the token dance ran, and nothing here could see whether it worked.
    """
    resolution = _WorkingResolution()

    await _resolve(_context(), resolution)

    assert resolution.context_while_resolving is _DELEGATED
    assert resolution.captured["auth_actor"] is _DELEGATED
    assert get_current_context() is None


@pytest.mark.asyncio
async def test_resolve_github_credential_names_the_projects_connected_account() -> None:
    """A project names the account it works as; resolution must be told,
    rather than falling back to whichever account happens to resolve for the
    user (ambiguous when they connected GitHub more than once)."""
    resolution = _WorkingResolution(email=None)
    account_id = uuid4()

    credential = await _resolve(_project_context(account_id=account_id), resolution)

    assert credential is not None
    assert credential.email is None
    assert resolution.captured["account_id"] == account_id
