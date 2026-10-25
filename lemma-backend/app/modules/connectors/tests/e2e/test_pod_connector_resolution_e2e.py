"""The account a pod connector runs as, after the second review of #1039.

Each test is one finding, end to end: the deprecated execute declares the
caller's own connector and never runs as somebody else; a standing binding
keeps the account it pinned; repointing a connector cannot hand a RESTRICTED
account to anyone it was not shared with; a departed member's account stops at
once and the editors are told; and the writes behind all of it -- a pod
account's last use, a racing declaration -- behave under load.

Reuses the world of ``test_pod_connectors_e2e.py``: one HTTP installation, its
provider patched to report which account's credential reached it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import select, update
from starlette import status

from app.core.authorization.service import AuthorizationDataService
from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkClass, WorkRegistry
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.channels.infrastructure.models import ChannelMessageModel
from app.modules.connectors.contracts.pod_connectors import (
    PodConnectorMode,
    pod_connector_for_account,
    resolve_standing_pod_connector,
)
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.domain.errors import ConnectorAccessDeniedError
from app.modules.connectors.domain.pod_connector import PodConnectorEntity
from app.modules.connectors.domain.pod_connector_errors import (
    ConnectorNeedsAccountError,
    PodConnectorConflictError,
)
from app.modules.connectors.events.member_left_work import register_member_left_work
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.pod_connector import (
    PodAccount,
    PodConnector,
)
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.connectors.infrastructure.repositories.pod_connector_repository import (
    PodAccountRepository,
    PodConnectorRepository,
)
from app.modules.connectors.services.pod_connector_notices import (
    tell_editors_connectors_lost_their_account,
)
from app.modules.connectors.tests.e2e.test_pod_connectors_e2e import (
    World,
    _connect,
    _pod_accounts,
    _run_installation,
    _world,
)

pytestmark = pytest.mark.e2e


async def _second_account(db_session, world: World, user: dict, secret: str) -> str:
    """Another account of ``user``'s on the same installation, not their default."""
    install = await db_session.get(AuthConfig, UUID(world.auth_config_id))
    created = await AccountRepository(
        SqlAlchemyUnitOfWork(db_session), encryption=get_secret_cipher()
    ).create(
        AccountEntity(
            user_id=UUID(user["id"]),
            organization_id=install.organization_id,
            auth_config_id=install.id,
            connector_id=install.connector_id,
            is_default=False,
            provider_account_id=secret,
            credentials={"api_key": secret},
        )
    )
    await db_session.commit()
    return str(created.id)


async def _share(world: World, user: dict, account_id: str, **body) -> str:
    shared = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts",
        user,
        json={"account_id": account_id, "visibility": "POD", **body},
    )
    assert shared.status_code == 201, shared.text
    return shared.json()["id"]


async def _ctx(db_session, world: World, user: dict):
    return await AuthorizationDataService(db_session).build_user_context(
        user_id=UUID(user["id"]), pod_id=UUID(world.pod_id)
    )


async def _standing(db_session, world: World, slug: str, **kwargs):
    return await resolve_standing_pod_connector(
        SqlAlchemyUnitOfWork(db_session),
        pod_id=UUID(world.pod_id),
        slug=slug,
        record_use=False,
        **kwargs,
    )


# -- implicit access and the deprecated execute ------------------------------


async def test_the_deprecated_execute_declares_the_callers_own_connector(
    scenario, db_session
):
    """The pod declares nothing for the installation: the caller's own
    account serves them, through a PER_MEMBER connector declared on this use
    -- the one helper every implicit caller shares."""
    world = await _world(scenario, db_session)
    await _connect(db_session, world, world.alice, "alice-key")

    ran = await _run_installation(world, world.alice, pod_id=world.pod_id)

    assert ran.status_code == 200, ran.text
    assert ran.json()["result"] == {"ran_as": "alice-key"}
    db_session.expire_all()
    [declared] = (
        await db_session.execute(
            select(PodConnector).where(PodConnector.pod_id == UUID(world.pod_id))
        )
    ).scalars()
    assert (declared.slug, declared.mode, declared.source) == (
        world.installation_name,
        "PER_MEMBER",
        "AUTO",
    )


async def test_the_deprecated_execute_never_runs_as_somebody_else(scenario, db_session):
    """It stepped past a 403 or 409 in slug order, so an account needing
    reauth -- or a SHARED slug sorting first -- ran the call as the team
    inbox. The caller's own PER_MEMBER answer is the answer."""
    world = await _world(scenario, db_session)
    bob_account = await _connect(db_session, world, world.bob, "team-inbox")
    team = await _share(world, world.bob, bob_account)
    # Sorts before `crm`, which is what the old loop tried first.
    assert (
        await world.declare("aaa-team", "SHARED", shared_pod_account_id=team)
    ).status_code == 201
    assert (await world.declare("crm", "PER_MEMBER")).status_code == 201
    alice_account = await _connect(db_session, world, world.alice, "alice-key")
    await db_session.execute(
        update(Account)
        .where(Account.id == UUID(alice_account))
        .values(status="REAUTH_REQUIRED")
    )
    await db_session.commit()

    expired = await _run_installation(world, world.alice, pod_id=world.pod_id)
    assert expired.json()["code"] == "REAUTH_REQUIRED", expired.text

    # No account of their own at all: told to connect one, not served by Bob's.
    none = await _run_installation(world, world.editor, pod_id=world.pod_id)
    assert none.json()["code"] == "CONNECTION_REQUIRED", none.text

    # Naming the shared account is how to reach it.
    named = await _run_installation(
        world, world.editor, pod_id=world.pod_id, account_id=bob_account
    )
    assert named.json()["result"] == {"ran_as": "team-inbox"}, named.text


# -- standing bindings pin their account --------------------------------------


async def test_a_standing_binding_keeps_the_account_it_pinned(scenario, db_session):
    """Bot B's schedule was migrated to the slug and then resolved to the
    owner's default, bot A: B went quiet and A fired twice."""
    world = await _world(scenario, db_session)
    bot_a = await _connect(db_session, world, world.alice, "bot-a")
    bot_b = await _second_account(db_session, world, world.alice, "bot-b")
    assert (await world.declare("crm", "PER_MEMBER")).status_code == 201
    assert (await world.run("crm", world.alice)).json()["result"] == {"ran_as": "bot-a"}

    binding = await pod_connector_for_account(
        SqlAlchemyUnitOfWork(db_session),
        await _ctx(db_session, world, world.alice),
        pod_id=UUID(world.pod_id),
        account_id=UUID(bot_b),
    )
    await db_session.commit()
    assert binding.slug == "crm" and binding.pod_account_id is not None

    alice = UUID(world.alice["id"])
    pinned = await _standing(
        db_session,
        world,
        "crm",
        on_behalf_of=alice,
        pod_account_id=binding.pod_account_id,
    )
    assert pinned.account_id == UUID(bot_b)
    unpinned = await _standing(db_session, world, "crm", on_behalf_of=alice)
    assert unpinned.account_id == UUID(bot_a)
    # A pin is only ever its own person's: on anyone else's binding it is
    # ignored, and their own default serves.
    bob_key = await _connect(db_session, world, world.bob, "bob-key")
    assert (await world.run("crm", world.bob)).status_code == 200
    as_bob = await _standing(
        db_session,
        world,
        "crm",
        on_behalf_of=UUID(world.bob["id"]),
        pod_account_id=binding.pod_account_id,
    )
    assert as_bob.account_id == UUID(bob_key)


async def test_a_surfaces_deprecated_account_becomes_a_shared_connector(
    scenario, db_session
):
    """It mapped the binder's own account to a PER_MEMBER slug, which a
    surface refuses, and to a 404 when the pod declared nothing. A surface
    runs with nobody behind it: the binder's own bot is shared, as binding it
    did before."""
    world = await _world(scenario, db_session)
    bot = await _connect(db_session, world, world.alice, "alice-bot")
    uow = SqlAlchemyUnitOfWork(db_session)

    first = await pod_connector_for_account(
        uow,
        await _ctx(db_session, world, world.alice),
        pod_id=UUID(world.pod_id),
        account_id=UUID(bot),
        mode=PodConnectorMode.SHARED,
    )
    await db_session.commit()
    again = await pod_connector_for_account(
        uow,
        await _ctx(db_session, world, world.bob),
        pod_id=UUID(world.pod_id),
        account_id=UUID(bot),
        mode=PodConnectorMode.SHARED,
    )

    assert again == first and first.pod_account_id is None
    connector = await PodConnectorRepository(uow).get_by_slug(
        UUID(world.pod_id), first.slug
    )
    assert connector is not None and connector.mode.value == "SHARED"
    [pod_account] = await _pod_accounts(db_session, world.pod_id)
    assert pod_account.visibility == "POD"
    # Somebody else's account that nobody shared is not reachable this way.
    carol_bot = await _connect(db_session, world, world.editor, "editor-bot")
    with pytest.raises(ConnectorAccessDeniedError):
        await pod_connector_for_account(
            uow,
            await _ctx(db_session, world, world.bob),
            pod_id=UUID(world.pod_id),
            account_id=UUID(carol_bot),
            mode=PodConnectorMode.SHARED,
        )


# -- repointing and RESTRICTED accounts ---------------------------------------


async def test_repointing_needs_the_account_to_be_shared_with_the_editor(
    scenario, db_session
):
    """`pod.connector.manage` let an editor point a SHARED slug at a
    RESTRICTED account, and every binding on the slug moved with it."""
    world = await _world(scenario, db_session)
    alice_account = await _connect(db_session, world, world.alice, "alice-key")
    restricted = await _share(
        world,
        world.alice,
        alice_account,
        visibility="RESTRICTED",
        share_with=[
            {
                "grantee_type": "POD_MEMBER",
                "grantee_id": world.seats["bob"]["pod_member_id"],
            }
        ],
    )

    pointed = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connectors",
        world.editor,
        json={
            "slug": "team",
            "auth_config_id": world.auth_config_id,
            "mode": "SHARED",
            "shared_pod_account_id": restricted,
        },
    )
    assert pointed.status_code == status.HTTP_403_FORBIDDEN, pointed.text

    shared_with_all = await world.call(
        "PATCH",
        f"/pods/{world.pod_id}/connector-accounts/{restricted}",
        world.alice,
        json={"visibility": "POD"},
    )
    assert shared_with_all.status_code == 200, shared_with_all.text
    pointed = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connectors",
        world.editor,
        json={
            "slug": "team",
            "auth_config_id": world.auth_config_id,
            "mode": "SHARED",
            "shared_pod_account_id": restricted,
        },
    )
    assert pointed.status_code == 201, pointed.text


async def test_a_standing_binding_gets_a_restricted_account_only_if_given_it(
    scenario, db_session
):
    """The rule a run with no person behind it obeys: POD-visible accounts
    only; a binding with a person, what that person was shared."""
    world = await _world(scenario, db_session)
    alice_account = await _connect(db_session, world, world.alice, "alice-key")
    pod_account = await _share(world, world.alice, alice_account)
    assert (
        await world.declare("team", "SHARED", shared_pod_account_id=pod_account)
    ).status_code == 201
    assert (await _standing(db_session, world, "team")).account_id == UUID(
        alice_account
    )

    narrowed = await world.call(
        "PATCH",
        f"/pods/{world.pod_id}/connector-accounts/{pod_account}",
        world.alice,
        json={
            "visibility": "RESTRICTED",
            "share_with": [
                {
                    "grantee_type": "POD_MEMBER",
                    "grantee_id": world.seats["bob"]["pod_member_id"],
                }
            ],
        },
    )
    assert narrowed.status_code == 200, narrowed.text
    db_session.expire_all()

    for on_behalf_of in (None, UUID(world.editor["id"])):
        with pytest.raises(ConnectorNeedsAccountError):
            await _standing(db_session, world, "team", on_behalf_of=on_behalf_of)
    for allowed in (world.bob, world.alice):
        resolved = await _standing(
            db_session, world, "team", on_behalf_of=UUID(allowed["id"])
        )
        assert resolved.account_id == UUID(alice_account)


# -- departure -----------------------------------------------------------------


async def _notes_for(db_session, pod_id: str, user_id: str) -> list[str]:
    db_session.expire_all()
    rows = await db_session.execute(
        select(ChannelMessageModel.body).where(
            ChannelMessageModel.pod_id == UUID(pod_id),
            ChannelMessageModel.on_behalf_user_id == UUID(user_id),
        )
    )
    return list(rows.scalars())


@pytest.mark.drives_work
async def test_a_departed_owners_account_stops_at_once_and_editors_are_told(
    scenario, db_session
):
    world = await _world(scenario, db_session)
    alice_account = await _connect(db_session, world, world.alice, "alice-key")
    shared = await _share(world, world.alice, alice_account)
    assert (
        await world.declare("telemetry", "SHARED", shared_pod_account_id=shared)
    ).status_code == 201

    left = await scenario.owner_client.delete(
        f"/pods/{world.pod_id}/members/{world.seats['alice']['pod_member_id']}"
    )
    assert left.status_code == 204, left.text
    # Before the cleanup job: her credential already serves nobody.
    before = await world.run("telemetry", world.bob)
    assert before.json()["code"] == "CONNECTOR_NEEDS_ACCOUNT", before.text
    with pytest.raises(ConnectorNeedsAccountError):
        await _standing(db_session, world, "telemetry")

    registry = WorkRegistry()
    register_member_left_work(registry)
    await run_until_idle(
        async_session_maker, registry=registry, classes=[WorkClass.EVENTS]
    )

    [note] = await _notes_for(db_session, world.pod_id, world.editor["id"])
    assert "'telemetry'" in note
    assert await _notes_for(db_session, world.pod_id, world.bob["id"]) == []
    # Said once, however often the cleanup is retried.
    async with SqlAlchemyUnitOfWork(db_session) as uow:
        await tell_editors_connectors_lost_their_account(
            uow,
            pod_id=UUID(world.pod_id),
            departed_user_id=UUID(world.alice["id"]),
            slugs=["telemetry"],
        )
        await uow.commit()
    assert len(await _notes_for(db_session, world.pod_id, world.editor["id"])) == 1


# -- the writes underneath -----------------------------------------------------


async def test_last_used_is_written_at_most_every_few_minutes(scenario, db_session):
    """It was stamped on every call, holding a busy shared account's row lock
    for the call's length."""
    world = await _world(scenario, db_session)
    await _connect(db_session, world, world.alice, "alice-key")
    assert (await world.declare("crm", "PER_MEMBER")).status_code == 201
    assert (await world.run("crm", world.alice)).status_code == 200
    [row] = await _pod_accounts(db_session, world.pod_id)
    first = row.last_used_at
    assert first is not None

    repository = PodAccountRepository(SqlAlchemyUnitOfWork(db_session))
    await repository.touch(row.to_entity())
    await db_session.commit()
    [row] = await _pod_accounts(db_session, world.pod_id)
    assert row.last_used_at == first, "a fresh stamp is not written again"

    stale = datetime.now(timezone.utc) - timedelta(minutes=10)
    await db_session.execute(
        update(PodAccount).where(PodAccount.id == row.id).values(last_used_at=stale)
    )
    await db_session.commit()
    [row] = await _pod_accounts(db_session, world.pod_id)
    await repository.touch(row.to_entity())
    await db_session.commit()
    [row] = await _pod_accounts(db_session, world.pod_id)
    assert row.last_used_at > stale


async def test_a_declaration_race_is_the_typed_conflict(scenario, db_session):
    world = await _world(scenario, db_session)
    repository = PodConnectorRepository(SqlAlchemyUnitOfWork(db_session))

    def declaration() -> PodConnectorEntity:
        return PodConnectorEntity(
            pod_id=UUID(world.pod_id),
            slug="crm",
            auth_config_id=UUID(world.auth_config_id),
            mode=PodConnectorMode.PER_MEMBER,
        )

    await repository.create(declaration())
    with pytest.raises(PodConnectorConflictError):
        await repository.create(declaration())
    # The loser's transaction is intact.
    assert (await repository.get_by_slug(UUID(world.pod_id), "crm")) is not None
    await db_session.commit()
