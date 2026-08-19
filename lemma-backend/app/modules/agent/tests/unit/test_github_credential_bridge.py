from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.modules.agent.services.workspace_location import ProjectRepo
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli import github_credential_bridge as bridge
from app.modules.agent.tools.workspace_cli import workspace_cli
from app.modules.agent.tools.workspace_cli.models import ExecCommandRequest
from app.modules.connectors.domain.errors import (
    AccountResolutionError,
    ConnectorAccessDeniedError,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def exists(self, key: str) -> int:
        return 1 if key in self.values else 0

    async def set(self, key: str, value: str, *, ex: int):
        del ex
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
async def test_ensure_github_credentials_skips_when_already_provisioned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _FakeRedis()
    redis.values[_MARKER_KEY] = "provisioned"
    monkeypatch.setattr(bridge, "get_redis", lambda url=None: redis)

    async def _fail_resolve(ctx):
        raise AssertionError("must not resolve a credential when already provisioned")

    monkeypatch.setattr(bridge, "_resolve_github_credential", _fail_resolve)

    session = _FakeCredentialSession()
    await bridge.ensure_github_credentials(_context(), session)

    assert session.written == []
    assert session.commands == []


@pytest.mark.asyncio
async def test_ensure_github_credentials_caches_no_account_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _FakeRedis()
    monkeypatch.setattr(bridge, "get_redis", lambda url=None: redis)

    async def _no_account(ctx):
        return None

    monkeypatch.setattr(bridge, "_resolve_github_credential", _no_account)

    session = _FakeCredentialSession()
    await bridge.ensure_github_credentials(_context(), session)

    assert session.written == []
    assert session.commands == []
    assert redis.values[_MARKER_KEY] == "unavailable"


@pytest.mark.asyncio
async def test_ensure_github_credentials_writes_credential_file_and_marks_provisioned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _FakeRedis()
    monkeypatch.setattr(bridge, "get_redis", lambda url=None: redis)

    async def _credential(ctx):
        return bridge._GithubCredential(
            access_token="gho_faketoken123", login="octocat", email="octocat@example.com"
        )

    monkeypatch.setattr(bridge, "_resolve_github_credential", _credential)

    session = _FakeCredentialSession()
    await bridge.ensure_github_credentials(_context(), session)

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


@pytest.mark.asyncio
async def test_ensure_github_credentials_falls_back_to_noreply_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A private-email account (GitHub's "keep my email address private")
    still needs a git-committable email -- fall back to GitHub's own noreply
    convention rather than leaving user.email unset."""
    redis = _FakeRedis()
    monkeypatch.setattr(bridge, "get_redis", lambda url=None: redis)

    async def _credential(ctx):
        return bridge._GithubCredential(
            access_token="gho_faketoken123", login="octocat", email=None
        )

    monkeypatch.setattr(bridge, "_resolve_github_credential", _credential)

    session = _FakeCredentialSession()
    await bridge.ensure_github_credentials(_context(), session)

    cmd = session.commands[0]
    assert "user.name octocat" in cmd
    assert "user.email octocat@users.noreply.github.com" in cmd


@pytest.mark.asyncio
async def test_ensure_github_credentials_skips_identity_setup_without_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An account connected before profile enrichment existed has no login on
    file -- the credential file still gets written (git/gh still work), it
    just can't configure a commit identity for itself."""
    redis = _FakeRedis()
    monkeypatch.setattr(bridge, "get_redis", lambda url=None: redis)

    async def _credential(ctx):
        return bridge._GithubCredential(access_token="gho_faketoken123", login=None, email=None)

    monkeypatch.setattr(bridge, "_resolve_github_credential", _credential)

    session = _FakeCredentialSession()
    await bridge.ensure_github_credentials(_context(), session)

    cmd = session.commands[0]
    assert "user.name" not in cmd
    assert "user.email" not in cmd


@pytest.mark.asyncio
async def test_ensure_github_credentials_noop_without_session_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def _get_redis(url=None):
        nonlocal called
        called = True
        return _FakeRedis()

    monkeypatch.setattr(bridge, "get_redis", _get_redis)

    session = _FakeCredentialSession(session_id=None)
    await bridge.ensure_github_credentials(_context(), session)

    assert called is False


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
async def test_exec_command_internal_invokes_bridge_only_for_git_like_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _RecordingWorkspaceSession()
    monkeypatch.setattr(
        workspace_cli, "get_workspace_tool_runtime", lambda: _RecordingRuntime(session)
    )

    calls: list[str] = []

    async def _fake_ensure(ctx, workspace_session):
        del ctx, workspace_session
        calls.append("called")

    monkeypatch.setattr(workspace_cli, "ensure_github_credentials", _fake_ensure)

    await workspace_cli.exec_command_internal(_context(), ExecCommandRequest(cmd="pwd"))
    assert calls == []

    await workspace_cli.exec_command_internal(
        _context(), ExecCommandRequest(cmd="git status")
    )
    assert calls == ["called"]


@pytest.mark.asyncio
async def test_exec_command_internal_runs_command_even_if_bridge_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _RecordingWorkspaceSession()
    monkeypatch.setattr(
        workspace_cli, "get_workspace_tool_runtime", lambda: _RecordingRuntime(session)
    )

    async def _broken_bridge(ctx, workspace_session):
        del ctx, workspace_session
        raise RuntimeError("redis is down")

    monkeypatch.setattr(workspace_cli, "ensure_github_credentials", _broken_bridge)

    result = await workspace_cli.exec_command_internal(
        _context(), ExecCommandRequest(cmd="git push")
    )

    # A broken credential bridge must not fail the underlying command --
    # it should still run (and, without credentials, fail with its own
    # native git auth error, not a generic workspace-tool error).
    assert result.success is True
    assert result.error is None


# --------------------------------------------------------------------------
# `_resolve_github_credential` itself. Every test above stubs this function
# out entirely, so none of them ever reach its own account-resolution/error
# handling. `SessionUnitOfWorkFactory(async_session_maker)` is hard-coded
# inside it rather than injected, but entering and exiting that unit of work
# touches no network: nothing here ever executes a query, so the async
# session is created and closed without a real Postgres connection --
# `build_delegated_context` and `get_account_resolution_service` (imported
# inside the function to avoid a cycle, so patched at their source module) are
# the only things stubbed.


def _project_context(*, account_id: UUID | None = None) -> BaseAgentContext:
    ctx = _context()
    if account_id is not None:
        ctx.workspace_repo = ProjectRepo(owner="acme", repo="widgets", account_id=account_id)
    return ctx


async def _fake_build_delegated_context(uow, ctx):
    del uow, ctx
    return SimpleNamespace(organization_id=None)


def _patch_account_resolution(monkeypatch: pytest.MonkeyPatch, service) -> None:
    monkeypatch.setattr(bridge, "build_delegated_context", _fake_build_delegated_context)
    monkeypatch.setattr(
        "app.modules.connectors.api.dependencies.get_account_resolution_service",
        lambda uow: service,
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
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """Neither "no account connected" nor "not authorized" is an error the
    caller should see -- both mean the same thing to a git command: there is
    no credential to provision, so `ensure_github_credentials` caches
    "unavailable" and lets the underlying command run without one."""

    class _DenyingResolution:
        async def resolve_account(self, **kwargs):
            del kwargs
            raise error

    _patch_account_resolution(monkeypatch, _DenyingResolution())

    result = await bridge._resolve_github_credential(_context())

    assert result is None


@pytest.mark.asyncio
async def test_resolve_github_credential_returns_none_without_an_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _NoTokenResolution:
        async def resolve_account(self, **kwargs):
            del kwargs
            return SimpleNamespace(
                credentials=SimpleNamespace(),  # no access_token at all
                display_name="octocat",
                email="octocat@example.com",
            )

    _patch_account_resolution(monkeypatch, _NoTokenResolution())

    result = await bridge._resolve_github_credential(_context())

    assert result is None


@pytest.mark.asyncio
async def test_resolve_github_credential_returns_a_credential_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _WorkingResolution:
        async def resolve_account(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                credentials=SimpleNamespace(access_token="gho_realtoken"),
                display_name="octocat",
                email="octocat@example.com",
            )

    _patch_account_resolution(monkeypatch, _WorkingResolution())
    ctx = _context()

    credential = await bridge._resolve_github_credential(ctx)

    assert credential == bridge._GithubCredential(
        access_token="gho_realtoken", login="octocat", email="octocat@example.com"
    )
    assert captured["user_id"] == ctx.user_id
    assert captured["connector_id"] == "github"
    # No project repo, so no account is named -- resolution falls back to
    # picking for the user (ambiguous only if they connected GitHub twice).
    assert captured["account_id"] is None


@pytest.mark.asyncio
async def test_resolve_github_credential_names_the_projects_connected_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A project names the account it works as; resolution must be told,
    rather than falling back to whichever account happens to resolve for the
    user (ambiguous when they connected GitHub more than once)."""
    captured: dict[str, object] = {}

    class _WorkingResolution:
        async def resolve_account(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                credentials=SimpleNamespace(access_token="gho_realtoken"),
                display_name="octocat",
                email=None,
            )

    _patch_account_resolution(monkeypatch, _WorkingResolution())
    account_id = uuid4()
    ctx = _project_context(account_id=account_id)

    credential = await bridge._resolve_github_credential(ctx)

    assert credential is not None
    assert credential.email is None
    assert captured["account_id"] == account_id
