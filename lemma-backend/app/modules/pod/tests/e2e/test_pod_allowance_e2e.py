"""A plan's pod allowance, held against a real database.

Pods count per person -- every pod they own, in every organization that is not
paid for some other way -- so each test signs up someone new: a shared user
would carry the pods of every test before it.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from app.core.ports.plan_limits import PodAllowance
from app.modules.test_support.e2e_authz import auth_headers, signup_user
from app.modules.test_support.plan_limits import SetPlan, plan

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

__all__ = ["plan"]


async def _person_with_an_organization(client: AsyncClient) -> tuple[dict, str]:
    person = await signup_user(client, "pod-allowance")
    response = await client.post(
        "/organizations",
        json={"name": f"Allowance {uuid4().hex[:8]}"},
        headers=auth_headers(person),
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return person, response.json()["id"]


async def _make_pod(client: AsyncClient, person: dict, organization_id: str):
    return await client.post(
        "/pods",
        json={
            "organization_id": organization_id,
            "name": f"Pod {uuid4().hex[:8]}",
            "type": "HYBRID",
        },
        headers=auth_headers(person),
    )


async def _pods_owned(client: AsyncClient, person: dict, organization_id: str) -> int:
    """What the person already owns there -- the refusal's own count is the
    thing under test, so it is not where the starting number comes from."""
    response = await client.get(
        f"/organizations/{organization_id}/pods?limit=100",
        headers=auth_headers(person),
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    return len(response.json()["items"])


async def test_a_person_at_their_allowance_is_refused_another_pod(
    async_client: AsyncClient, plan: SetPlan
):
    person, organization_id = await _person_with_an_organization(async_client)
    owned = await _pods_owned(async_client, person, organization_id)
    plan.pods = PodAllowance(limit=owned + 2)
    for _ in range(2):
        made = await _make_pod(async_client, person, organization_id)
        assert made.status_code == status.HTTP_201_CREATED, made.text

    refused = await _make_pod(async_client, person, organization_id)

    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text
    body = refused.json()
    assert body["code"] == "POD_LIMIT_REACHED"
    assert body["details"] == {"limit": owned + 2, "used": owned + 2}
    assert await _pods_owned(async_client, person, organization_id) == owned + 2


async def test_deleting_a_pod_frees_its_place(async_client: AsyncClient, plan: SetPlan):
    person, organization_id = await _person_with_an_organization(async_client)
    owned = await _pods_owned(async_client, person, organization_id)
    plan.pods = PodAllowance(limit=owned + 1)
    made = await _make_pod(async_client, person, organization_id)
    assert made.status_code == status.HTTP_201_CREATED, made.text
    assert (await _make_pod(async_client, person, organization_id)).status_code == 403

    deleted = await async_client.delete(
        f"/pods/{made.json()['id']}", headers=auth_headers(person)
    )
    assert deleted.status_code < 300, deleted.text

    again = await _make_pod(async_client, person, organization_id)
    assert again.status_code == status.HTTP_201_CREATED, again.text


async def test_pods_where_the_plan_does_not_count_them_are_not_counted(
    async_client: AsyncClient, plan: SetPlan
):
    """A team plan pays for its own pods, so a person's pods there do not use
    up what their own plan allows elsewhere."""
    person, team_organization_id = await _person_with_an_organization(async_client)
    response = await async_client.post(
        "/organizations",
        json={"name": f"Own {uuid4().hex[:8]}"},
        headers=auth_headers(person),
    )
    own_organization_id = response.json()["id"]
    team = UUID(team_organization_id)
    for _ in range(2):
        assert (
            await _make_pod(async_client, person, team_organization_id)
        ).status_code == 201
    plan.pods = PodAllowance(
        limit=1 + await _pods_owned(async_client, person, own_organization_id),
        excluded_organization_ids=frozenset({team}),
    )

    made = await _make_pod(async_client, person, own_organization_id)

    assert made.status_code == status.HTTP_201_CREATED, made.text


async def test_two_pods_made_at_once_cannot_both_take_the_last_place(
    async_client: AsyncClient, plan: SetPlan
):
    person, organization_id = await _person_with_an_organization(async_client)
    plan.pods = PodAllowance(
        limit=1 + await _pods_owned(async_client, person, organization_id)
    )

    answers = await asyncio.gather(
        _make_pod(async_client, person, organization_id),
        _make_pod(async_client, person, organization_id),
    )

    assert sorted(answer.status_code for answer in answers) == [201, 403], [
        answer.text for answer in answers
    ]


async def test_with_no_plan_declared_nothing_is_limited(async_client: AsyncClient):
    person, organization_id = await _person_with_an_organization(async_client)

    for _ in range(3):
        made = await _make_pod(async_client, person, organization_id)
        assert made.status_code == status.HTTP_201_CREATED, made.text
