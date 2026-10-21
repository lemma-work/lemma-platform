"""Listing what a trigger skipped: newest first, a page at a time, schedule readers only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from app.modules.schedule.infrastructure.models.trigger import TriggerSkip
from app.modules.schedule.tests.e2e.test_schedule_e2e import (
    _create_agent,
    _create_pod,
    _create_schedule,
)
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e


async def _time_schedule(client: AsyncClient, pod_id: str) -> dict:
    agent = await _create_agent(client, pod_id)
    return await _create_schedule(
        client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent["name"],
        config={"cron": "0 0 * * *"},
    )


async def test_skips_page_newest_first_and_only_for_readers_of_the_schedule(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    db_manager,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await _time_schedule(authenticated_client, pod_id)
    schedule_id = UUID(schedule["id"])
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    async with db_manager.session_factory() as session:
        # Two rows share an instant: the page boundary must still split them.
        for index, at in enumerate([0, 1, 2, 2, 3]):
            session.add(
                TriggerSkip(
                    schedule_id=schedule_id,
                    reason="bulk_write" if index % 2 == 0 else "import_write",
                    first=f"{100 + index}:0",
                    last=f"{100 + index}:9",
                    count=10 * (index + 1),
                    at=base + timedelta(minutes=at),
                )
            )
        await session.commit()

    path = f"/pods/{pod_id}/schedules/{schedule_id}/skips"
    seen: list[dict] = []
    token: str | None = None
    for _ in range(5):
        params: dict[str, str | int] = {"limit": 2}
        if token:
            params["page_token"] = token
        page = await authenticated_client.get(path, params=params)
        assert page.status_code == 200, page.text
        body = page.json()
        assert len(body["items"]) <= 2
        seen.extend(body["items"])
        token = body["next_page_token"]
        if token is None:
            break
    assert [item["count"] for item in seen] == [50, 40, 30, 20, 10]
    assert len({item["id"] for item in seen}) == 5
    assert seen[0]["reason"] == "bulk_write"
    assert seen[0]["first"] == "104:0" and seen[0]["last"] == "104:9"

    bad_token = await authenticated_client.get(path, params={"page_token": "nope"})
    assert bad_token.status_code == 400, bad_token.text

    # Another pod's path never reaches this schedule.
    other_pod = await _create_pod(authenticated_client, fixed_test_org["id"])
    cross = await authenticated_client.get(
        f"/pods/{other_pod}/schedules/{schedule_id}/skips"
    )
    assert cross.status_code == 404, cross.text
    missing = await authenticated_client.get(
        f"/pods/{pod_id}/schedules/{uuid4()}/skips"
    )
    assert missing.status_code == 404, missing.text

    # An org member outside the pod is refused.
    outsider = await signup_user(async_client, "skips-outsider")
    await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=outsider,
    )
    refused = await async_client.get(path, headers=auth_headers(outsider))
    assert refused.status_code in (403, 404), refused.text


async def test_a_peer_cannot_list_the_skips_of_a_personal_schedule(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)
    headers: list[dict[str, str]] = []
    for slug in ("skips-owner", "skips-peer"):
        user = await signup_user(async_client, slug)
        member = await invite_org_member(
            authenticated_client,
            async_client,
            org_id=fixed_test_org["id"],
            user=user,
        )
        await add_pod_member(
            authenticated_client,
            pod_id=pod_id,
            organization_member_id=member["id"],
            role="POD_USER",
            roles=["POD_USER"],
        )
        headers.append(auth_headers(user))
    owner_headers, peer_headers = headers
    personal = await _create_schedule(
        async_client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent["name"],
        config={"cron": "0 0 * * *"},
        headers=owner_headers,
    )
    assert personal["visibility"] == "PERSONAL"
    path = f"/pods/{pod_id}/schedules/{personal['id']}/skips"

    owner = await async_client.get(path, headers=owner_headers)
    assert owner.status_code == 200, owner.text
    assert owner.json() == {"items": [], "limit": 50, "next_page_token": None}
    peer = await async_client.get(path, headers=peer_headers)
    assert peer.status_code == 403, peer.text
