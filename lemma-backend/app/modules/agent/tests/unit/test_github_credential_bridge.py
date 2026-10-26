from __future__ import annotations

from contextlib import asynccontextmanager
from functools import partial
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.authorization.current import get_current_context
from app.modules.agent.domain.workspace_location import ProjectRepo
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.tools.workspace_cli import github_credential_bridge as bridge
from app.modules.agent.tools.workspace_cli import github_project
from app.modules.agent.tools.workspace_cli import workspace_cli
from app.modules.agent.tools.workspace_cli.models import ExecCommandRequest
from app.core.domain.errors import DomainError
from app.modules.connectors.contracts.pod_connectors import (
    ConnectionRequiredError,
    ConnectorNeedsAccountError,
    PodConnectorNotFoundError,
    ReauthRequiredError,
)
from app.modules.connectors.domain.errors import ConnectorAccessDeniedError

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

    async def get(self, key: str) -> str | None:
        self.calls.append(("get", key))
        return self.values.get(key)

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


def _context() -> ToolContext:
    return ToolContext(user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4())


def _credential(**overrides):
    """A resolver returning one credential, recording who asked."""
    calls: list[ToolContext] = []

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
    calls: list[ToolContext] = []

    async def _resolve(ctx):
        calls.append(ctx)
        return

    _resolve.calls = calls  # type: ignore[attr-defined]
    return _resolve


def _must_not_resolve():
    async def _resolve(ctx):
        raise AssertionError("must not resolve a credential on this path")

    return _resolve


# Per session: which account is written is the pod connector's decision for
# the person acting, so nothing about the conversation enters the key.
_MARKER_KEY = f"{bridge._MARKER_KEY_PREFIX}:session-1"


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
    assert written["/tmp/lemma-sess/session-1/git-credentials"] == (
        b"https://x-access-token:gho_faketoken123@github.com\n"
    )
    # `gh` does not read git's credential file. Writing its own config is what
    # makes `gh` authenticate as the same account, without the token entering
    # the environment where `env` would print it into a tool result.
    hosts = written["/tmp/lemma-sess/session-1/gh/hosts.yml"]
    assert b"oauth_token: gho_faketoken123" in hosts
    assert b"user: octocat" in hosts
    gitconfig = written["/tmp/lemma-sess/session-1/gitconfig"].decode()
    assert (
        'helper = "store --file=/tmp/lemma-sess/session-1/git-credentials"' in gitconfig
    )
    assert 'name = "octocat"' in gitconfig
    assert 'email = "octocat@example.com"' in gitconfig
    assert "gho_faketoken123" not in gitconfig

    assert len(session.commands) == 1
    cmd = session.commands[0]
    # Both credential files are delivered via write_file, never interpolated
    # into a shell string that could end up in process listings or command
    # logs. The permissions are what the command is for.
    assert "gho_faketoken123" not in cmd
    assert "git config" not in cmd
    assert "chmod 600 /tmp/lemma-sess/session-1/gh/hosts.yml" in cmd
    # Ended sessions' directories go, each of which held a credential any
    # process in the sandbox could read; only ones nobody refreshed for twice
    # the longest a live session goes between writes.
    assert (
        "(find /tmp/lemma-sess -mindepth 1 -maxdepth 1 -type d "
        f"-mmin +{2 * bridge._PROVISIONED_TTL_SECONDS // 60} -exec rm -rf {{}} +"
    ) in cmd
    assert redis.values[_MARKER_KEY] == "provisioned"
    assert ("set", _MARKER_KEY, "provisioned", bridge._PROVISIONED_TTL_SECONDS) in (
        redis.calls
    )


@pytest.mark.asyncio
async def test_a_refusal_reaches_the_agent_once_with_its_remedy() -> None:
    """No credential is not silence any more: the pod connector says why --
    connect your account, reconnect it, the pod declares no `github` -- and
    the agent sees that, link included, the first time it reaches for git.
    The refusal is cached like before, so the next command says nothing."""
    redis = _FakeRedis()
    session = _FakeCredentialSession()
    refusal = bridge.GithubRefusal(
        code="CONNECTION_REQUIRED",
        message="Connect your github account to use 'github' in this pod.",
        connect_url="https://app.example/pods/p/connections?connector=github",
    )

    async def _refuse(ctx):
        del ctx
        return refusal

    notice = await bridge.ensure_github_credentials(
        _context(), session, redis=redis, resolve_credential=_refuse
    )

    assert notice is not None
    assert "Connect your github account" in notice
    assert "https://app.example/pods/p/connections?connector=github" in notice
    assert session.written == []
    assert redis.values[_MARKER_KEY] == "unavailable"
    assert (
        await bridge.ensure_github_credentials(
            _context(), session, redis=redis, resolve_credential=_must_not_resolve()
        )
        is None
    )


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

    gitconfig = dict(session.written)["/tmp/lemma-sess/session-1/gitconfig"]
    assert b'name = "octocat"' in gitconfig
    assert b'email = "octocat@users.noreply.github.com"' in gitconfig


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

    written = dict(session.written)
    assert "/tmp/lemma-sess/session-1/git-credentials" in written
    gitconfig = written["/tmp/lemma-sess/session-1/gitconfig"]
    assert b"[user]" not in gitconfig


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


@pytest.mark.asyncio
async def test_two_sessions_in_one_sandbox_never_share_a_credential() -> None:
    """Every agent session of a person runs in their one sandbox. A credential
    file or a `git config --global` the sandbox shares was whichever session
    wrote last, so one conversation pushed as the account another resolved."""
    from app.modules.workspace.contracts.session_config import session_config_env

    first = _FakeCredentialSession(session_id="conversation-a")
    second = _FakeCredentialSession(session_id="conversation-b")
    for session in (first, second):
        await bridge.ensure_github_credentials(
            _context(), session, redis=_FakeRedis(), resolve_credential=_credential()
        )

    first_paths = {path for path, _ in first.written}
    second_paths = {path for path, _ in second.written}
    assert not first_paths & second_paths
    assert all(p.startswith("/tmp/lemma-sess/conversation-a/") for p in first_paths)
    assert all(p.startswith("/tmp/lemma-sess/conversation-b/") for p in second_paths)
    # Nothing global: no shared file, no `git config --global`.
    for session in (first, second):
        assert "git config" not in " ".join(session.commands)
        assert "/tmp/.git-credentials" not in {p for p, _ in session.written}

    # Each session's environment names its own files, and so only its own.
    env_a = session_config_env("conversation-a")
    env_b = session_config_env("conversation-b")
    assert env_a["GIT_CONFIG_VALUE_0"] in first_paths
    assert env_b["GIT_CONFIG_VALUE_0"] in second_paths
    assert env_a["GH_CONFIG_DIR"] != env_b["GH_CONFIG_DIR"]
    assert "GIT_CONFIG_GLOBAL" not in env_a


def test_a_session_id_cannot_climb_out_of_its_directory() -> None:
    from app.modules.workspace.contracts.session_config import (
        SESSION_CONFIG_ROOT,
        session_config_dir,
    )

    hostile = session_config_dir("../../home/user")
    lookalike = session_config_dir("..__home_user")

    assert hostile.startswith(f"{SESSION_CONFIG_ROOT}/")
    assert "/" not in hostile.removeprefix(f"{SESSION_CONFIG_ROOT}/")
    assert ".." not in hostile
    assert hostile != lookalike


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
# none of them reach its own pod-connector resolution or error handling. Its
# unit of work and connector collaborators are arguments, so these open no
# database session.

_ACTING = uuid4()


async def _fake_tool_authorization_context(uow, ctx):
    del uow
    # What `acting_person` reads: no Person actor, the delegating person.
    return SimpleNamespace(
        actor=None, delegating_user_id=ctx.user_id, organization_id=None
    )


def _uow_factory():
    @asynccontextmanager
    async def _scope():
        yield SimpleNamespace(session=None)

    return lambda: _scope()


async def _passthrough_refresh(_uow, account, _user_id):
    """Stand in for the refresh, returning what the account already holds.

    The real one builds a `ConnectorService`, which is the single step in this
    function that needs a live unit of work -- so it is a collaborator, and
    these tests stay about the bridge's own decisions.
    """
    credentials = getattr(account, "credentials", None)
    if credentials is None:
        return {}
    return {
        key: value
        for key, value in vars(credentials).items()
        if not key.startswith("_")
    }


async def _resolve(ctx, connector, refresh=_passthrough_refresh):
    return await bridge._resolve_github_credential(
        ctx,
        uow_factory=_uow_factory(),
        delegated_context=_fake_tool_authorization_context,
        resolve_connector=connector,
        refresh_credentials=refresh,
    )


class _Resolved:
    """The pod's `github` connector, resolving to one account."""

    def __init__(
        self,
        *,
        owner: UUID | None = None,
        email: str | None = None,
        allow_sandbox_use: bool = False,
        pod_account_id: UUID | None = None,
    ):
        self.calls: list[dict[str, object]] = []
        self.context_while_resolving: object = "not-set"
        self._owner = owner
        self._email = email
        self._allow_sandbox_use = allow_sandbox_use
        self.pod_account_id = pod_account_id or uuid4()

    async def __call__(self, uow, auth_ctx, *, pod_id, slug):
        del uow
        self.calls.append({"auth_ctx": auth_ctx, "pod_id": pod_id, "slug": slug})
        self.context_while_resolving = get_current_context()
        return SimpleNamespace(
            owner_user_id=self._owner or auth_ctx.delegating_user_id,
            pod_account_id=self.pod_account_id,
            allow_sandbox_use=self._allow_sandbox_use,
            account=SimpleNamespace(
                credentials=SimpleNamespace(access_token="gho_realtoken"),
                display_name="octocat",
                email=self._email,
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ConnectionRequiredError(
            slug="github",
            connector_id="github",
            connect_url="https://app.example/pods/p/connections?connector=github",
        ),
        ReauthRequiredError(
            slug="github",
            connector_id="github",
            connect_url="https://app.example/pods/p/connections?connector=github",
            acting_person_is_owner=True,
        ),
        PodConnectorNotFoundError("github"),
        ConnectorNeedsAccountError(slug="github", connect_url="https://app.test/c"),
        ConnectorAccessDeniedError("not authorized"),
    ],
)
async def test_a_typed_refusal_comes_back_as_data(error: DomainError) -> None:
    """Whatever the pod connector refuses with is the agent's to read, with the
    connect link when there is one -- never an exception that only a log sees."""

    async def _refusing(uow, auth_ctx, *, pod_id, slug):
        del uow, auth_ctx, pod_id, slug
        raise error

    refusal = await _resolve(_context(), _refusing)

    assert isinstance(refusal, bridge.GithubRefusal)
    assert refusal.code == error.code
    assert refusal.message == error.message
    details = error.details if isinstance(error.details, dict) else {}
    assert refusal.connect_url == details.get("connect_url")


@pytest.mark.asyncio
async def test_a_shared_account_is_never_written_into_a_sandbox() -> None:
    """A SHARED `github` connector runs as somebody else's account. A token in a
    sandbox is a token every later command can read, so only the acting
    person's own account is put there, and nothing is refreshed for one that
    is not."""

    async def _must_not_refresh(*_args):
        raise AssertionError("must not touch a borrowed account's token")

    refusal = await _resolve(_context(), _Resolved(owner=uuid4()), _must_not_refresh)

    assert isinstance(refusal, bridge.GithubRefusal)
    assert refusal.code == "SHARED_ACCOUNT_NOT_FOR_SANDBOX"
    assert refusal.connect_url is None


@pytest.mark.asyncio
async def test_a_shared_account_its_owner_allowed_in_sandboxes_is_written() -> None:
    """The owner's opt-in ("allow use inside sandboxes") is the one way a
    borrowed account's token reaches a workspace (design §3.8), and the
    credential remembers whose it is, for the session's marker."""
    lent = _Resolved(owner=uuid4(), allow_sandbox_use=True)
    ctx = _context()

    credential = await _resolve(ctx, lent)

    assert credential == bridge._GithubCredential(
        access_token="gho_realtoken",
        login="octocat",
        email=None,
        borrowed_pod_account_id=lent.pod_account_id,
        borrower_user_id=ctx.user_id,
    )


@pytest.mark.asyncio
async def test_resolve_github_credential_returns_none_without_an_access_token() -> None:
    async def _no_token(uow, auth_ctx, *, pod_id, slug):
        del uow, pod_id, slug
        return SimpleNamespace(
            owner_user_id=auth_ctx.delegating_user_id,
            account=SimpleNamespace(
                credentials=SimpleNamespace(), display_name="octocat", email=None
            ),
        )

    assert await _resolve(_context(), _no_token) is None


@pytest.mark.asyncio
async def test_the_pods_github_connector_is_what_resolves() -> None:
    resolved = _Resolved(email="octocat@example.com")
    ctx = _context()

    credential = await _resolve(ctx, resolved)

    assert credential == bridge._GithubCredential(
        access_token="gho_realtoken", login="octocat", email="octocat@example.com"
    )
    assert resolved.calls[0]["pod_id"] == ctx.pod_id
    assert resolved.calls[0]["slug"] == "github"


@pytest.mark.asyncio
async def test_resolve_github_credential_resolves_under_the_delegated_context() -> None:
    """The connector layer authorizes off the context it is handed and the
    *current* one, so the delegated one has to be installed for the call and
    taken back down after it."""
    resolved = _Resolved()

    await _resolve(_context(), resolved)

    delegated = resolved.calls[0]["auth_ctx"]
    assert resolved.context_while_resolving is delegated
    assert get_current_context() is None


# The token the sandbox is handed is a *copy*, so how long it is good for is
# this module's problem and not only the connector layer's.


@pytest.mark.asyncio
async def test_the_written_token_is_the_refreshed_one() -> None:
    """The bridge used to read `account.credentials` verbatim while the refresh
    machinery sat unused one module away, so an expired token was re-written
    into the workspace every 45 minutes for as long as the session lasted."""

    async def _refresh(_uow, _account, _user_id):
        return {"access_token": "gho_refreshed"}

    credential = await _resolve(_context(), _Resolved(), _refresh)

    assert credential is not None
    assert credential.access_token == "gho_refreshed"


@pytest.mark.asyncio
async def test_the_numeric_id_comes_from_the_stored_profile() -> None:
    """`provider_account_id` is the login for this connector, and the noreply
    address that survives a rename is keyed by the number instead."""

    async def _refresh(_uow, _account, _user_id):
        return {
            "access_token": "gho_realtoken",
            "user_data": {"profile": {"id": 583231, "login": "octocat"}},
        }

    credential = await _resolve(_context(), _Resolved(), _refresh)

    assert credential is not None
    assert credential.user_id == "583231"
    assert (
        bridge._noreply_email(credential) == "583231+octocat@users.noreply.github.com"
    )


def test_the_noreply_address_survives_a_rename_only_with_the_id() -> None:
    without = bridge._GithubCredential(
        access_token="t", login="octocat", email=None, user_id=None
    )
    assert bridge._noreply_email(without) == "octocat@users.noreply.github.com"
    assert (
        bridge._noreply_email(
            bridge._GithubCredential(access_token="t", login=None, email=None)
        )
        is None
    )


class TestProvisioningWindow:
    """How long a copy written into the sandbox may be trusted."""

    def _credential(self, expires_in_seconds: float | None):
        from datetime import timedelta

        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
            if expires_in_seconds is not None
            else None
        )
        return bridge._GithubCredential(
            access_token="t", login="octocat", email=None, expires_at=expires_at
        )

    def test_a_credential_with_no_stated_expiry_keeps_the_flat_ceiling(self) -> None:
        assert bridge._provisioned_ttl(self._credential(None)) == 45 * 60

    def test_a_long_lived_token_is_still_capped_by_the_ceiling(self) -> None:
        """Eight hours is the GitHub App user-token lifetime, and the ceiling is
        the safety net for things an expiry cannot see: a disconnected account,
        a revoked token."""
        assert bridge._provisioned_ttl(self._credential(8 * 3600)) == 45 * 60

    def test_a_token_dying_sooner_shortens_the_window(self) -> None:
        ttl = bridge._provisioned_ttl(self._credential(20 * 60))
        assert ttl == pytest.approx(20 * 60 - 5 * 60, abs=2)

    def test_an_expired_token_is_not_trusted_for_45_minutes(self) -> None:
        """The bug this whole change is about: without an expiry-derived
        window, a dead token was rewritten on a wall clock and believed."""
        assert bridge._provisioned_ttl(self._credential(-1)) == 30


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "credential_notice, checkout_notice, expected",
    [
        ("no github", None, "no github"),
        (None, "clone failed", "clone failed"),
        ("no github", "clone failed", "no github\nclone failed"),
        (None, None, None),
    ],
)
async def test_the_project_step_shows_the_credential_refusal_too(
    credential_notice, checkout_notice, expected
) -> None:
    """Why the clone failed is usually why there was no credential, so the
    agent is told both, in that order."""

    async def _credentials(ctx, workspace_session):
        del ctx, workspace_session
        return credential_notice

    async def _checkout(ctx, workspace_session):
        del ctx, workspace_session
        return checkout_notice

    notice = await github_project.prepare_project_directory(
        _context(),
        _FakeCredentialSession(),
        wanted=True,
        ensure_credentials=_credentials,
        ensure_checkout=_checkout,
    )

    assert notice == expected
