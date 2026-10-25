"""Leaving the organization takes a member's accounts with them.

Audit M11, the organization half. Removing someone from the organization
cascades their pod memberships away in the database, but nothing announced it:
their org accounts stayed connected, their pod accounts stayed in every pod,
the grants sharing those pod accounts stayed, and a SHARED connector kept
running on their credential. Only removing them from each pod first cleaned
anything up.

Now an ``OrganizationMemberRemoved`` fact drives one job: the per-pod cleanup a
pod departure already does, for every pod in the organization they had an
account in, and then their org accounts themselves -- revoked at the provider
where that is possible, and deleted. A shared connector that ran on one goes
NEEDS_ACCOUNT, as it does when the owner leaves a pod. That is the line the
design draws between leaving and being downgraded: a downgraded owner's shared
accounts keep serving the pod, a departed owner's do not.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import WorkClass, WorkRegistry
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.connectors.events.member_left_work import register_member_left_work
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.pod_connector import PodAccount
from app.modules.connectors.tests.e2e.test_pod_connectors_e2e import (
    _connect,
    _pod_accounts,
    _world,
)
from app.modules.test_support.e2e_authz import add_pod_member

pytestmark = pytest.mark.e2e


async def _drain() -> None:
    registry = WorkRegistry()
    register_member_left_work(registry)
    await run_until_idle(
        async_session_maker, registry=registry, classes=[WorkClass.EVENTS]
    )


async def _org_member_id(scenario, org_id: str, user: dict) -> str:
    members = await scenario.owner_client.get(f"/organizations/{org_id}/members")
    assert members.status_code == 200, members.text
    return next(
        item["id"]
        for item in members.json()["items"]
        if item.get("user", {}).get("email") == user["email"]
    )


async def _grants_on(db_session, resource_ids: list[UUID]) -> list[tuple]:
    from app.core.authorization.models import ResourcePermissionGrantModel

    db_session.expire_all()
    rows = await db_session.execute(
        select(
            ResourcePermissionGrantModel.resource_type,
            ResourcePermissionGrantModel.resource_id,
        ).where(ResourcePermissionGrantModel.resource_id.in_(resource_ids))
    )
    return list(rows.all())


@pytest.mark.drives_work
async def test_leaving_the_org_takes_a_members_accounts_out_of_every_pod(
    scenario, db_session
):
    world = await _world(scenario, db_session)
    alice_account = await _connect(db_session, world, world.alice, "alice-key")

    # Shared with Bob, and the admin who points a connector at it (repointing
    # needs the account shared with whoever does it), by grant: the grants
    # must go with the pod account.
    admin_seat = await scenario.current_pod_member_id()
    shared = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts",
        world.alice,
        json={
            "account_id": alice_account,
            "visibility": "RESTRICTED",
            "share_with": [
                {
                    "grantee_type": "POD_MEMBER",
                    "grantee_id": world.seats["bob"]["pod_member_id"],
                },
                {"grantee_type": "POD_MEMBER", "grantee_id": admin_seat},
            ],
        },
    )
    assert shared.status_code == 201, shared.text
    pod_account_id = UUID(shared.json()["id"])
    declared = await world.declare(
        "telemetry", "SHARED", shared_pod_account_id=str(pod_account_id)
    )
    assert declared.status_code == 201, declared.text
    assert (await world.run("telemetry", world.bob)).json()["result"] == {
        "ran_as": "alice-key"
    }
    assert await _grants_on(db_session, [pod_account_id]) != []

    # A second pod in the same organization, where she uses it as her own.
    first_pod = world.scenario.pod
    await world.scenario.create_pod(name_prefix="Second")
    second_pod_id = world.pod_id
    alice_member = await _org_member_id(scenario, scenario.org_id, world.alice)
    await add_pod_member(
        scenario.owner_client,
        pod_id=second_pod_id,
        organization_member_id=alice_member,
        role="POD_USER",
    )
    assert (await world.declare("crm", "PER_MEMBER")).status_code == 201
    assert (await world.run("crm", world.alice)).status_code == 200
    assert len(await _pod_accounts(db_session, second_pod_id)) == 1
    world.scenario.pod = first_pod

    removed = await scenario.owner_client.delete(
        f"/organizations/{scenario.org_id}/members/{alice_member}"
    )
    assert removed.status_code == 204, removed.text
    await _drain()

    assert await _pod_accounts(db_session, world.pod_id) == []
    assert await _pod_accounts(db_session, second_pod_id) == []
    assert await _grants_on(db_session, [pod_account_id]) == []
    after = await world.run("telemetry", world.bob)
    assert after.json()["code"] == "CONNECTOR_NEEDS_ACCOUNT", after.text
    # Her org account is gone too: nothing runs on her credential any more.
    db_session.expire_all()
    assert await db_session.get(Account, UUID(alice_account)) is None


@pytest.mark.drives_work
async def test_leaving_one_org_leaves_the_members_other_orgs_alone(
    scenario, db_session
):
    """Accounts belong to the person *within* an organization. Leaving one is
    not leaving the others."""
    world = await _world(scenario, db_session)
    org_one = scenario.org_id
    here = await _connect(db_session, world, world.alice, "alice-here")

    elsewhere_world = await _world(scenario, db_session)
    elsewhere = await _connect(
        db_session, elsewhere_world, world.alice, "alice-elsewhere"
    )
    # `_world` made a fresh org and pod; put Alice in that pod as well.
    await elsewhere_world.scenario.add_user_to_pod(user=world.alice, role="POD_USER")
    assert (await elsewhere_world.declare("crm", "PER_MEMBER")).status_code == 201
    assert (await elsewhere_world.run("crm", world.alice)).status_code == 200

    alice_member = await _org_member_id(scenario, org_one, world.alice)
    removed = await scenario.owner_client.delete(
        f"/organizations/{org_one}/members/{alice_member}"
    )
    assert removed.status_code == 204, removed.text
    await _drain()

    db_session.expire_all()
    assert await db_session.get(Account, UUID(here)) is None
    assert await db_session.get(Account, UUID(elsewhere)) is not None
    kept = await db_session.execute(
        select(PodAccount).where(PodAccount.account_id == UUID(elsewhere))
    )
    assert len(list(kept.scalars())) == 1
