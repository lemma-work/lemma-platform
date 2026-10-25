""" "Share as <slug>": share my account and run a SHARED connector as it.

It used to be two requests from the page -- widen the account to the pod, then
declare or point the connector -- so a refused second step left the account
shared with everyone and nothing running as it. One request now: every check
first, both changes in one transaction.
"""

from __future__ import annotations

import pytest
from starlette import status

from app.modules.connectors.tests.e2e.test_pod_connectors_e2e import (
    World,
    _connect,
    _world,
)

pytestmark = pytest.mark.e2e


async def _mine_in_the_pod(world: World, db_session, user: dict) -> str:
    account_id = await _connect(db_session, world, user, f"{user['id']}-key")
    added = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts",
        user,
        json={"account_id": account_id},
    )
    assert added.status_code == status.HTTP_201_CREATED, added.text
    assert added.json()["visibility"] == "PERSONAL"
    return added.json()["id"]


async def _visibility(world: World, user: dict, pod_account_id: str) -> str:
    listing = await world.call("GET", f"/pods/{world.pod_id}/connector-accounts", user)
    assert listing.status_code == status.HTTP_200_OK, listing.text
    [mine] = [one for one in listing.json()["items"] if one["id"] == pod_account_id]
    return mine["visibility"]


async def test_sharing_as_a_new_connector_shares_and_declares_together(
    scenario, db_session
):
    world = await _world(scenario, db_session)
    pod_account_id = await _mine_in_the_pod(world, db_session, world.editor)

    shared = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts/{pod_account_id}/share-as",
        world.editor,
        json={"slug": "crm-team", "purpose": "The team inbox"},
    )

    assert shared.status_code == status.HTTP_200_OK, shared.text
    assert shared.json()["mode"] == "SHARED"
    assert shared.json()["shared_pod_account_id"] == pod_account_id
    assert shared.json()["status"] == "READY"
    assert await _visibility(world, world.editor, pod_account_id) == "POD"


async def test_a_refused_connector_step_leaves_the_account_unshared(
    scenario, db_session
):
    """Alice owns the account but may not manage the pod's connectors: the
    connector step would be refused, so her account is not widened first."""
    world = await _world(scenario, db_session)
    pod_account_id = await _mine_in_the_pod(world, db_session, world.alice)

    refused = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts/{pod_account_id}/share-as",
        world.alice,
        json={"slug": "crm-team"},
    )

    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text
    assert await _visibility(world, world.alice, pod_account_id) == "PERSONAL"


async def test_a_per_member_slug_is_refused_before_anything_is_shared(
    scenario, db_session
):
    world = await _world(scenario, db_session)
    declared = await world.declare("crm", "PER_MEMBER")
    assert declared.status_code == status.HTTP_201_CREATED, declared.text
    pod_account_id = await _mine_in_the_pod(world, db_session, world.editor)

    refused = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts/{pod_account_id}/share-as",
        world.editor,
        json={"slug": "crm"},
    )

    assert refused.status_code in (400, 422), refused.text
    assert "not_shared_mode" in refused.text
    assert await _visibility(world, world.editor, pod_account_id) == "PERSONAL"


async def test_only_the_accounts_owner_shares_it(scenario, db_session):
    world = await _world(scenario, db_session)
    pod_account_id = await _mine_in_the_pod(world, db_session, world.alice)

    refused = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts/{pod_account_id}/share-as",
        world.editor,
        json={"slug": "crm-team"},
    )

    assert refused.status_code in (403, 404), refused.text
    assert await _visibility(world, world.alice, pod_account_id) == "PERSONAL"
