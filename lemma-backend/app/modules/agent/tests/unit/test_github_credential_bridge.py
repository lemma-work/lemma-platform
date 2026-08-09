from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli import github_credential_bridge as bridge
from app.modules.agent.tools.workspace_cli import workspace_cli
from app.modules.agent.tools.workspace_cli.models import ExecCommandRequest


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

    assert session.written == [
        (
            "/tmp/.git-credentials",
            b"https://x-access-token:gho_faketoken123@github.com\n",
        )
    ]
    assert len(session.commands) == 1
    cmd = session.commands[0]
    assert "credential.helper" in cmd
    assert "/tmp/.git-credentials" in cmd
    assert "user.name octocat" in cmd
    assert "user.email octocat@example.com" in cmd
    # The token is delivered via write_file, never interpolated into a shell
    # string that could end up in process listings or command logs.
    assert "gho_faketoken123" not in cmd
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
