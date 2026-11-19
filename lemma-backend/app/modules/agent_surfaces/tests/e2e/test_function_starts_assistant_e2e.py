"""A function starting the Lemma's own assistant for the person it runs as.

Starting the assistant is authorized against the pod, so the grant is the
pod's `agent.execute` (`pod:/`), as a pod link's is. `agent:pod_default:execute`
-- the spelling a builder reaches for -- used to be accepted and never match;
it is stored as the same grant. The person's own access still bounds the run.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.modules.identity.infrastructure.supertokens_auth.helpers import (
    get_user_token,
)
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)

pytestmark = pytest.mark.e2e

_ON_THE_POD = {
    "resource_type": "pod",
    "resource_name": "/",
    "permission_ids": ["agent.execute"],
}
_BY_ITS_NAME = {
    "resource_type": "agent",
    "resource_name": "pod_default",
    "permission_ids": ["agent.execute"],
}


async def _function(client: AsyncClient, pod_id: str, grants: list[dict]) -> dict:
    name = f"daily_{uuid4().hex[:8]}"
    created = await client.post(
        f"/pods/{pod_id}/functions",
        json={"name": name, "description": "Starts the day"},
    )
    assert created.status_code == 201, created.text
    granted = await client.put(
        f"/pods/{pod_id}/functions/{name}/permissions", json={"grants": grants}
    )
    assert granted.status_code == 200, granted.text
    return created.json()


async def _as_function(
    test_app, *, function: dict, pod_id: str, user_id: str
) -> AsyncClient:
    claims = build_delegation_claims(
        workload_type="function",
        workload_id=UUID(function["id"]),
        pod_id=UUID(pod_id),
        session_id=uuid4().hex,
        invoked_by_user_id=UUID(user_id),
        workload_name=function["name"],
    )
    token = await get_user_token(UUID(user_id), delegation_claims=claims)
    return AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.parametrize("grant", [_ON_THE_POD, _BY_ITS_NAME], ids=["pod", "by-name"])
async def test_a_granted_function_starts_the_assistant_for_its_person(
    test_app, authenticated_client: AsyncClient, fixed_test_user, test_pod, grant
):
    pod_id = test_pod["id"]
    function = await _function(authenticated_client, pod_id, [grant])

    held = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{function['name']}/permissions"
    )
    # Either spelling is stored, and exported, as the pod's own grant.
    assert [
        (g["resource_type"], g["resource_name"], g["permission_ids"])
        for g in held.json()["grants"]
    ] == [("pod", "/", ["agent.execute"])]

    async with await _as_function(
        test_app, function=function, pod_id=pod_id, user_id=fixed_test_user["id"]
    ) as run:
        started = await run.post(
            f"/pods/{pod_id}/conversations", json={"title": "Plan the day"}
        )

    assert started.status_code == 201, started.text


async def test_without_the_grant_a_function_cannot_start_the_assistant(
    test_app, authenticated_client: AsyncClient, fixed_test_user, test_pod
):
    pod_id = test_pod["id"]
    function = await _function(authenticated_client, pod_id, [])

    async with await _as_function(
        test_app, function=function, pod_id=pod_id, user_id=fixed_test_user["id"]
    ) as run:
        refused = await run.post(
            f"/pods/{pod_id}/conversations", json={"title": "Plan the day"}
        )

    assert refused.status_code == 403, refused.text
