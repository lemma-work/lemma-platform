"""``act_as: app`` on the pod execute route: a function's to ask, and the app
or nothing.

A function posts as the person it runs for unless it asks to present as the
connector's app -- the only identity GitHub takes a check run from. Through
the HTTP API, with the real context: a person asking is refused, a function
asking where there is no app to be is refused before the provider is called,
and a function saying nothing runs as the person exactly as before.

The GitHub presenter's own cases (an installation token minted, a user-only
route, an account with no installation) are in ``test_github_presenter.py``.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from starlette import status

from app.modules.connectors.tests.e2e.test_pod_connectors_e2e import (
    _EXECUTOR,
    World,
    _connect,
    _echo_the_credential,
    _world,
)
from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)

pytestmark = pytest.mark.e2e


async def _function_headers(world: World, user: dict) -> dict[str, str]:
    """A function granted the ``crm`` connector, running for ``user``."""
    owner = world.scenario.owner_client
    name = f"checks_{uuid4().hex[:8]}"
    created = await owner.post(
        f"/pods/{world.pod_id}/functions",
        json={"name": name, "description": "Posts a check"},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    granted = await owner.put(
        f"/pods/{world.pod_id}/functions/{name}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "connector",
                    "resource_name": "crm",
                    "permission_ids": ["connector.use"],
                }
            ]
        },
    )
    assert granted.status_code == status.HTTP_200_OK, granted.text
    claims = build_delegation_claims(
        workload_type="function",
        workload_id=UUID(created.json()["id"]),
        workload_name=name,
        pod_id=UUID(world.pod_id),
        session_id=uuid4().hex,
        invoked_by_user_id=UUID(user["id"]),
    )
    token = await get_user_token(UUID(user["id"]), delegation_claims=claims)
    return {"Authorization": f"Bearer {token}"}


async def _run(world: World, headers: dict[str, str], body: dict):
    with patch(_EXECUTOR, new=_echo_the_credential) as executor:
        response = await world.scenario.async_client.post(
            f"/pods/{world.pod_id}/connectors/crm/operations/echo/execute",
            headers=headers,
            json=body,
        )
    return response, executor


async def test_a_person_asking_to_be_the_app_is_refused(scenario, db_session) -> None:
    world = await _world(scenario, db_session)
    await _connect(db_session, world, world.alice, "alice-key")
    assert (await world.declare("crm", "PER_MEMBER")).status_code == 201

    refused, _ = await _run(
        world, world.headers(world.alice), {"payload": {}, "act_as": "app"}
    )

    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text
    assert refused.json()["code"] == "APP_IDENTITY_FUNCTIONS_ONLY"


async def test_a_function_asking_where_there_is_no_app_is_refused_not_run_as_the_person(
    scenario, db_session
) -> None:
    """The connector has no app identity. Running the call with the person's
    credential instead would post under a name the function chose not to use."""
    world = await _world(scenario, db_session)
    await _connect(db_session, world, world.alice, "alice-key")
    assert (await world.declare("crm", "PER_MEMBER")).status_code == 201
    headers = await _function_headers(world, world.alice)

    refused, _ = await _run(world, headers, {"payload": {}, "act_as": "app"})

    assert refused.status_code == status.HTTP_409_CONFLICT, refused.text
    assert refused.json()["code"] == "APP_IDENTITY_UNAVAILABLE"


async def test_a_function_saying_nothing_still_runs_as_its_person(
    scenario, db_session
) -> None:
    world = await _world(scenario, db_session)
    await _connect(db_session, world, world.alice, "alice-key")
    assert (await world.declare("crm", "PER_MEMBER")).status_code == 201
    headers = await _function_headers(world, world.alice)

    ran, _ = await _run(world, headers, {"payload": {}})

    assert ran.status_code == status.HTTP_200_OK, ran.text
    assert ran.json()["result"] == {"ran_as": "alice-key"}
