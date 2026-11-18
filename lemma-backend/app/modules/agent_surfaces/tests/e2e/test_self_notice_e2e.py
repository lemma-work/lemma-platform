"""A function or agent telling the person it runs as something -- and nobody else.

``POST /pods/{pod}/members/me/notices`` takes ``channel.notify_self``, the one
channel action a named workload can be granted, on the pod named ``/``. These
drive it with real delegated tokens, the way a function's run calls back into
the API: the grant lets it write in its own person's channel; it still cannot
write to another member, and ``channel.write`` itself stays ungrantable.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.infrastructure.models import ChannelModel
from app.modules.identity.infrastructure.supertokens_auth.helpers import (
    get_user_token,
)
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e

_NOTIFY_SELF = {
    "resource_type": "pod",
    "resource_name": "/",
    "permission_ids": ["channel.notify_self"],
}


async def _function(client: AsyncClient, pod_id: str, grants: list[dict]) -> dict:
    name = f"morning_{uuid4().hex[:8]}"
    created = await client.post(
        f"/pods/{pod_id}/functions",
        json={"name": name, "description": "Writes the morning note"},
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
    """A client holding the token a run of ``function`` for ``user_id`` gets."""
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


async def _colleague(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    *,
    org_id: str,
    pod_id: str,
    role: str,
) -> dict[str, str]:
    person = await signup_user(async_client, "colleague")
    member = await invite_org_member(
        authenticated_client, async_client, org_id=org_id, user=person
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=member["id"],
        role=role,
    )
    return person


async def test_a_granted_function_tells_its_own_person_and_nobody_else(
    test_app,
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    fixed_test_user,
    fixed_test_org,
    test_pod,
):
    pod_id = test_pod["id"]
    colleague = await _colleague(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        role="POD_USER",
    )
    function = await _function(authenticated_client, pod_id, [_NOTIFY_SELF])

    # The grant reads back by the name a bundle writes.
    held = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{function['name']}/permissions"
    )
    assert held.status_code == 200, held.text
    assert [
        (g["resource_type"], g["resource_name"]) for g in held.json()["grants"]
    ] == [("pod", "/")]

    async with await _as_function(
        test_app, function=function, pod_id=pod_id, user_id=fixed_test_user["id"]
    ) as run:
        told = await run.post(
            f"/pods/{pod_id}/members/me/notices",
            json={"text": "Three things today."},
            headers={"Idempotency-Key": "morning-note"},
        )
        again = await run.post(
            f"/pods/{pod_id}/members/me/notices",
            json={"text": "Three things today."},
            headers={"Idempotency-Key": "morning-note"},
        )
        to_colleague = await run.post(
            f"/pods/{pod_id}/members/{colleague['email']}/notices",
            json={"text": "Not yours to say."},
        )

    assert told.status_code == 201, told.text
    assert again.json()["message_id"] == told.json()["message_id"]
    channel = await db_session.get(ChannelModel, UUID(told.json()["channel_id"]))
    assert channel is not None
    assert channel.member_user_id == UUID(fixed_test_user["id"])

    # Reaching anyone else is `channel.write`, which the function does not hold
    # and cannot be given.
    assert to_colleague.status_code == 403, to_colleague.text
    colleague_channels = await db_session.scalars(
        select(ChannelModel.id).where(
            ChannelModel.member_user_id == UUID(colleague["id"])
        )
    )
    for channel_id in colleague_channels:
        lines = await authenticated_client.get(
            f"/pods/{pod_id}/channels/{channel_id}/messages"
        )
        # Read, or the absence below would prove nothing.
        assert lines.status_code == 200, lines.text
        assert "Not yours to say." not in lines.text


async def test_channel_write_is_still_not_grantable_on_the_pod(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    name = f"wide_{uuid4().hex[:8]}"
    created = await authenticated_client.post(
        f"/pods/{pod_id}/functions", json={"name": name, "description": "x"}
    )
    assert created.status_code == 201, created.text

    refused = await authenticated_client.put(
        f"/pods/{pod_id}/functions/{name}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "pod",
                    "resource_name": "/",
                    "permission_ids": ["channel.write"],
                }
            ]
        },
    )
    assert refused.status_code == 400, refused.text
    assert "PERMISSION_RESOURCE_TYPE_MISMATCH" in refused.text

    # And the pod has no name but "/": a bundle cannot point at another pod.
    elsewhere = await authenticated_client.put(
        f"/pods/{pod_id}/functions/{name}/permissions",
        json={"grants": [{**_NOTIFY_SELF, "resource_name": str(uuid4())}]},
    )
    assert elsewhere.status_code == 400, elsewhere.text


async def test_without_the_grant_a_function_is_refused(
    test_app, authenticated_client: AsyncClient, fixed_test_user, test_pod
):
    pod_id = test_pod["id"]
    function = await _function(authenticated_client, pod_id, [])

    async with await _as_function(
        test_app, function=function, pod_id=pod_id, user_id=fixed_test_user["id"]
    ) as run:
        refused = await run.post(
            f"/pods/{pod_id}/members/me/notices", json={"text": "Morning."}
        )

    assert refused.status_code == 403, refused.text
    assert "MISSING_WORKLOAD_RESOURCE_GRANT" in refused.text


async def test_the_grant_never_lifts_a_person_who_cannot_write(
    test_app,
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    test_pod,
):
    """The person's access bounds the run: a viewer, who writes in no channel,
    gets nothing from a function holding the grant."""
    pod_id = test_pod["id"]
    viewer = await _colleague(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        role="POD_VIEWER",
    )
    function = await _function(authenticated_client, pod_id, [_NOTIFY_SELF])

    async with await _as_function(
        test_app, function=function, pod_id=pod_id, user_id=viewer["id"]
    ) as run:
        refused = await run.post(
            f"/pods/{pod_id}/members/me/notices", json={"text": "Morning."}
        )

    assert refused.status_code == 403, refused.text


async def test_a_member_writes_a_note_to_themself(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    fixed_test_user,
    test_pod,
):
    told = await authenticated_client.post(
        f"/pods/{test_pod['id']}/members/me/notices", json={"text": "Call the bank."}
    )
    assert told.status_code == 201, told.text
    channel = await db_session.get(ChannelModel, UUID(told.json()["channel_id"]))
    assert channel is not None
    assert channel.member_user_id == UUID(fixed_test_user["id"])
