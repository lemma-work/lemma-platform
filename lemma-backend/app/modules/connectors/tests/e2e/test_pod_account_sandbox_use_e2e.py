"""A shared pod account goes inside a sandbox only if its owner says so.

The sandbox's git credential bridge writes a GitHub token into a workspace,
where every later command can read it. Alice shared her account with the pod
for operations run through Lemma; whether a shell may hold it is a separate
question, and only she answers it: ``allow_sandbox_use`` on her pod account,
set through her own ``PATCH /pods/{pod}/connector-accounts/{id}``.

Everything below is real except the sandbox and its marker cache: the HTTP API,
the database, the pod connector resolver and the bridge's own decisions.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from starlette import status

from app.core.authorization.service import AuthorizationDataService
from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.workspace_cli import (
    github_credential_bridge as bridge,
)
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.connectors.tests.e2e.test_pod_connectors_e2e import World, _world
from app.modules.workspace.contracts.session_config import (
    session_git_credentials_path,
)

pytestmark = pytest.mark.e2e


class _Sandbox:
    """The workspace session: what was written into it."""

    def __init__(self) -> None:
        self.session_id = f"sandbox-{uuid4().hex[:8]}"
        self.written: dict[str, bytes] = {}

    async def write_file(self, path: str, data: bytes) -> None:
        self.written[path] = data

    async def exec_command(self, *, cmd: str, timeout: int | None = None):
        del cmd, timeout
        return {"success": True}


class _Markers:
    """The per-session marker cache, empty, so every call resolves afresh."""

    async def get(self, key: str) -> str | None:
        del key
        return None

    async def set(self, key: str, value: str, *, ex: int) -> None:
        del key, value, ex

    async def sadd(self, key: str, *values: str) -> None:
        del key, values

    async def expire(self, key: str, time: int) -> None:
        del key, time


async def _as_person(uow, ctx: ToolContext):
    return await AuthorizationDataService(uow.session).build_user_context(
        user_id=ctx.user_id, pod_id=ctx.pod_id
    )


async def _bridge_as(world: World, user: dict) -> tuple[_Sandbox, str | None]:
    """Run the bridge for ``user`` acting in the pod, as git would trigger it."""
    sandbox = _Sandbox()
    ctx = ToolContext(
        user_id=UUID(user["id"]), pod_id=UUID(world.pod_id), conversation_id=uuid4()
    )

    async def _resolve(agent_ctx: ToolContext):
        return await bridge._resolve_github_credential(
            agent_ctx,
            uow_factory=SessionUnitOfWorkFactory(async_session_maker),
            delegated_context=_as_person,
        )

    notice = await bridge.ensure_github_credentials(
        ctx, sandbox, redis=_Markers(), resolve_credential=_resolve
    )
    return sandbox, notice


async def _alices_shared_github(world: World, db_session) -> str:
    """Alice shares her account with the pod; `github` runs as it, SHARED."""
    install = await db_session.get(AuthConfig, UUID(world.auth_config_id))
    account = await AccountRepository(
        SqlAlchemyUnitOfWork(db_session), encryption=get_secret_cipher()
    ).create(
        AccountEntity(
            user_id=UUID(world.alice["id"]),
            organization_id=install.organization_id,
            auth_config_id=install.id,
            connector_id=install.connector_id,
            is_default=True,
            display_name="alice-gh",
            credentials={"access_token": "gho_alices_token"},
        )
    )
    await db_session.commit()
    account_id = str(account.id)
    shared = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts",
        world.alice,
        json={"account_id": account_id, "visibility": "POD"},
    )
    assert shared.status_code == status.HTTP_201_CREATED, shared.text
    pod_account_id = shared.json()["id"]
    assert shared.json()["allow_sandbox_use"] is False
    declared = await world.declare(
        "github", "SHARED", shared_pod_account_id=pod_account_id
    )
    assert declared.status_code == status.HTTP_201_CREATED, declared.text
    return pod_account_id


async def test_only_the_owner_can_allow_their_account_inside_sandboxes(
    scenario, db_session
):
    world = await _world(scenario, db_session)
    pod_account_id = await _alices_shared_github(world, db_session)
    path = f"/pods/{world.pod_id}/connector-accounts/{pod_account_id}"

    for somebody_else in (world.bob, world.editor):
        refused = await world.call(
            "PATCH", path, somebody_else, json={"allow_sandbox_use": True}
        )
        assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text
    # Nor a pod admin -- the org owner here.
    by_admin = await scenario.owner_client.patch(path, json={"allow_sandbox_use": True})
    assert by_admin.status_code == status.HTTP_403_FORBIDDEN, by_admin.text

    allowed = await world.call(
        "PATCH", path, world.alice, json={"allow_sandbox_use": True}
    )
    assert allowed.status_code == status.HTTP_200_OK, allowed.text
    assert allowed.json()["allow_sandbox_use"] is True


async def test_a_shared_account_reaches_a_sandbox_only_while_its_owner_allows_it(
    scenario, db_session
):
    world = await _world(scenario, db_session)
    pod_account_id = await _alices_shared_github(world, db_session)
    path = f"/pods/{world.pod_id}/connector-accounts/{pod_account_id}"

    # Without the opt-in Bob's sandbox gets nothing, and he is told why.
    sandbox, notice = await _bridge_as(world, world.bob)
    assert sandbox.written == {}
    assert notice is not None and "shared account is not put" in notice

    # With it, the shared account's token is what git finds.
    allowed = await world.call(
        "PATCH", path, world.alice, json={"allow_sandbox_use": True}
    )
    assert allowed.status_code == status.HTTP_200_OK, allowed.text
    sandbox, notice = await _bridge_as(world, world.bob)
    assert notice is None
    # Into the session's own credential store, not one every session reads.
    written = sandbox.written[session_git_credentials_path(sandbox.session_id)]
    assert b"gho_alices_token" in written

    # Alice withdraws it: nothing more is written, and the opt-in does not
    # outlive the sharing it was given for.
    withdrawn = await world.call(
        "PATCH", path, world.alice, json={"visibility": "PERSONAL"}
    )
    assert withdrawn.status_code == status.HTTP_200_OK, withdrawn.text
    assert withdrawn.json()["allow_sandbox_use"] is False
    sandbox, notice = await _bridge_as(world, world.bob)
    assert sandbox.written == {}
    # The connector itself now needs an account: the pod connector's own
    # refusal, not the sandbox rule, is what Bob reads.
    assert notice is not None and "shared account is not put" not in notice


async def test_turning_sandbox_use_off_says_existing_copies_are_swept(
    scenario, db_session
):
    """Sandboxes that already hold the token are not reached at once: the
    owner is told, in the response, that a copy stays until that session's
    next git command or the stale-session sweep -- and none is given out
    afterwards."""
    world = await _world(scenario, db_session)
    pod_account_id = await _alices_shared_github(world, db_session)
    path = f"/pods/{world.pod_id}/connector-accounts/{pod_account_id}"
    allowed = await world.call(
        "PATCH", path, world.alice, json={"allow_sandbox_use": True}
    )
    assert allowed.status_code == status.HTTP_200_OK, allowed.text
    assert allowed.json()["notice"] is None

    off = await world.call(
        "PATCH", path, world.alice, json={"allow_sandbox_use": False}
    )

    assert off.status_code == status.HTTP_200_OK, off.text
    assert off.json()["allow_sandbox_use"] is False
    assert "swept" in off.json()["notice"]
    sandbox, notice = await _bridge_as(world, world.bob)
    assert sandbox.written == {}
    assert notice is not None and "shared account is not put" in notice


async def test_sandbox_use_needs_the_account_shared_first(scenario, db_session):
    world = await _world(scenario, db_session)
    pod_account_id = await _alices_shared_github(world, db_session)
    path = f"/pods/{world.pod_id}/connector-accounts/{pod_account_id}"
    withdrawn = await world.call(
        "PATCH", path, world.alice, json={"visibility": "PERSONAL"}
    )
    assert withdrawn.status_code == status.HTTP_200_OK, withdrawn.text

    refused = await world.call(
        "PATCH", path, world.alice, json={"allow_sandbox_use": True}
    )

    assert refused.status_code == status.HTTP_400_BAD_REQUEST, refused.text
    assert refused.json()["details"]["reason"] == "account_not_shared"


# -- a loan holds only while the whole rule does --------------------------------


class _KeptMarkers:
    """A marker cache that remembers, and that nothing outside this test
    writes to: what a session's later git command finds is only what the
    bridge itself left, so the per-use re-check is all that is exercised."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> None:
        del ex
        self.values[key] = value

    async def sadd(self, key: str, *values: str) -> None:
        self.sets.setdefault(key, set()).update(values)

    async def srem(self, key: str, *values: str) -> None:
        self.sets.get(key, set()).difference_update(values)

    async def expire(self, key: str, time: int) -> None:
        del key, time


class _Sessions:
    """One person's sandbox sessions, each run through the bridge as git
    would trigger it, against one marker store."""

    def __init__(self, world: World, user: dict, markers) -> None:
        self.world = world
        self.user = user
        self.markers = markers

    async def git(self, sandbox: _Sandbox) -> str | None:
        ctx = ToolContext(
            user_id=UUID(self.user["id"]),
            pod_id=UUID(self.world.pod_id),
            conversation_id=uuid4(),
        )

        async def _resolve(agent_ctx: ToolContext):
            return await bridge._resolve_github_credential(
                agent_ctx,
                uow_factory=SessionUnitOfWorkFactory(async_session_maker),
                delegated_context=_as_person,
            )

        return await bridge.ensure_github_credentials(
            ctx, sandbox, redis=self.markers, resolve_credential=_resolve
        )


class _RecordingSandbox(_Sandbox):
    def __init__(self) -> None:
        super().__init__()
        self.commands: list[str] = []

    async def exec_command(self, *, cmd: str, timeout: int | None = None):
        del timeout
        self.commands.append(cmd)
        return {"success": True}

    def took_back(self) -> bool:
        return any(cmd.startswith("rm -f ") for cmd in self.commands)


async def _connect_github(world: World, db_session, user: dict, token: str) -> str:
    install = await db_session.get(AuthConfig, UUID(world.auth_config_id))
    account = await AccountRepository(
        SqlAlchemyUnitOfWork(db_session), encryption=get_secret_cipher()
    ).create(
        AccountEntity(
            user_id=UUID(user["id"]),
            organization_id=install.organization_id,
            auth_config_id=install.id,
            connector_id=install.connector_id,
            is_default=True,
            display_name=f"{token}-login",
            credentials={"access_token": token},
        )
    )
    await db_session.commit()
    return str(account.id)


async def _lend_to_sandboxes(world: World, pod_account_id: str) -> None:
    allowed = await world.call(
        "PATCH",
        f"/pods/{world.pod_id}/connector-accounts/{pod_account_id}",
        world.alice,
        json={"allow_sandbox_use": True},
    )
    assert allowed.status_code == status.HTTP_200_OK, allowed.text


async def test_a_lent_token_is_taken_back_once_the_connector_runs_as_another_account(
    scenario, db_session
):
    """The opt-in is still on, but an editor re-pointed `github` at another
    shared account: Alice's token no longer serves Bob, so his session's
    next git command removes it -- reading only the flag kept it."""
    world = await _world(scenario, db_session)
    alices = await _alices_shared_github(world, db_session)
    await _lend_to_sandboxes(world, alices)
    sessions = _Sessions(world, world.bob, _KeptMarkers())
    sandbox = _RecordingSandbox()
    assert await sessions.git(sandbox) is None
    assert (
        b"gho_alices_token"
        in sandbox.written[session_git_credentials_path(sandbox.session_id)]
    )

    editors_account = await _connect_github(world, db_session, world.editor, "gho_ed")
    shared = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts",
        world.editor,
        json={"account_id": editors_account, "visibility": "POD"},
    )
    assert shared.status_code == status.HTTP_201_CREATED, shared.text
    repointed = await world.call(
        "PATCH",
        f"/pods/{world.pod_id}/connectors/github",
        world.editor,
        json={"shared_pod_account_id": shared.json()["id"]},
    )
    assert repointed.status_code == status.HTTP_200_OK, repointed.text

    notice = await sessions.git(sandbox)

    assert sandbox.took_back()
    assert notice is not None and "no longer available" in notice


async def test_a_lent_token_is_taken_back_once_the_share_no_longer_reaches_them(
    scenario, db_session
):
    """Alice narrows a RESTRICTED share to leave Bob out. The opt-in is
    untouched, and the token in his session goes on its next git command."""
    world = await _world(scenario, db_session)
    alices = await _alices_shared_github(world, db_session)
    path = f"/pods/{world.pod_id}/connector-accounts/{alices}"

    def _member(name: str) -> dict:
        return {
            "grantee_type": "POD_MEMBER",
            "grantee_id": world.seats[name]["pod_member_id"],
        }

    restricted = await world.call(
        "PATCH",
        path,
        world.alice,
        json={"visibility": "RESTRICTED", "share_with": [_member("bob")]},
    )
    assert restricted.status_code == status.HTTP_200_OK, restricted.text
    await _lend_to_sandboxes(world, alices)
    sessions = _Sessions(world, world.bob, _KeptMarkers())
    sandbox = _RecordingSandbox()
    assert await sessions.git(sandbox) is None
    assert session_git_credentials_path(sandbox.session_id) in sandbox.written

    narrowed = await world.call(
        "PATCH",
        path,
        world.alice,
        json={"visibility": "RESTRICTED", "share_with": [_member("editor")]},
    )
    assert narrowed.status_code == status.HTTP_200_OK, narrowed.text
    assert narrowed.json()["allow_sandbox_use"] is True

    notice = await sessions.git(sandbox)

    assert sandbox.took_back()
    assert notice is not None and "no longer available" in notice


async def test_turning_sandbox_use_off_marks_every_session_that_borrowed_it(
    scenario, db_session
):
    """Every session holding a copy is marked once the change commits -- not
    only the one that happens to run git next -- and the owner is told that
    an ended session's copy waits for the stale-session sweep."""
    from app.core.config import settings
    from app.core.infrastructure.redis.client import get_redis
    from app.modules.connectors.contracts.sandbox_borrowers import (
        MARKER_WITHDRAWN,
        SANDBOX_STALE_SESSION_MINUTES,
        sandbox_credential_marker_key,
    )

    world = await _world(scenario, db_session)
    alices = await _alices_shared_github(world, db_session)
    await _lend_to_sandboxes(world, alices)
    redis = get_redis(url=settings.redis_url)
    sessions = _Sessions(world, world.bob, redis)
    first, second = _RecordingSandbox(), _RecordingSandbox()
    assert await sessions.git(first) is None
    assert await sessions.git(second) is None

    off = await world.call(
        "PATCH",
        f"/pods/{world.pod_id}/connector-accounts/{alices}",
        world.alice,
        json={"allow_sandbox_use": False},
    )

    assert off.status_code == status.HTTP_200_OK, off.text
    assert f"{SANDBOX_STALE_SESSION_MINUTES} minutes" in off.json()["notice"]
    for sandbox in (first, second):
        marker = await redis.get(sandbox_credential_marker_key(sandbox.session_id))
        assert marker == MARKER_WITHDRAWN
        assert await sessions.git(sandbox) is not None
        assert sandbox.took_back()
