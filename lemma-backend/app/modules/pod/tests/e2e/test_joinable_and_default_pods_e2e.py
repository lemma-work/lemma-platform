"""Finding the pods open to you, and the pods that take in everyone who arrives.

Against Postgres, because both are SQL over a pod's JSON config -- which join
policies count as open, which pods a member is already in, which take in new
organization members -- and the second is work written by identity's
transaction and run by the pod module's handler.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import WorkClass, WorkRegistry
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.pod.events.auto_join_work import (
    POD_AUTO_JOIN_ORG_MEMBER_ADDED,
    register_auto_join_work,
)
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e

#: A response body as the API sent it: it has no shape until a test asserts one.
Body = dict[str, Any]

OPEN_TO_MEMBERS: dict[str, object] = {"join_policy": "ORG_MEMBERS"}


async def _create_pod(
    client: AsyncClient,
    org_id: str,
    *,
    prefix: str,
    config: dict[str, object] | None = None,
    description: str | None = None,
) -> Body:
    payload: dict[str, object] = {
        "organization_id": org_id,
        "name": f"{prefix} {uuid4().hex[:8]}",
    }
    if description is not None:
        payload["description"] = description
    if config is not None:
        payload["config"] = config
    response = await client.post("/pods", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _joinable(
    client: AsyncClient, org_id: str, user: dict[str, str], **params: object
) -> Body:
    response = await client.get(
        f"/organizations/{org_id}/pods/joinable",
        headers=auth_headers(user),
        params=params,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _pods_of(client: AsyncClient, org_id: str, user: dict[str, str]) -> set[str]:
    response = await client.get(
        f"/organizations/{org_id}/pods", headers=auth_headers(user)
    )
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


async def _org_member(
    owner_client: AsyncClient, async_client: AsyncClient, org_id: str, prefix: str
) -> dict[str, str]:
    """A signed-up person who has accepted an invitation into the organization."""
    user = await signup_user(async_client, prefix)
    member = await invite_org_member(
        owner_client, async_client, org_id=org_id, user=user
    )
    return {**user, "org_member_id": member["id"]}


async def _run_default_pod_joins() -> None:
    registry = WorkRegistry()
    register_auto_join_work(registry)
    await run_until_idle(
        async_session_maker, registry=registry, classes=[WorkClass.EVENTS]
    )


async def _default_pod_join_items() -> list[str]:
    """Every default-pod join still in the queue, by dedup key, in any state."""
    async with async_session_maker() as session:
        rows = await session.execute(
            text("SELECT dedup_key FROM work_items WHERE kind = :kind"),
            {"kind": POD_AUTO_JOIN_ORG_MEMBER_ADDED},
        )
        return sorted(rows.scalars().all())


async def test_a_member_is_offered_the_open_pods_they_are_not_in(
    authenticated_client: AsyncClient, async_client: AsyncClient, fixed_test_org
):
    org_id = fixed_test_org["id"]
    invite_only = await _create_pod(authenticated_client, org_id, prefix="Closed")
    open_to_org = await _create_pod(
        authenticated_client,
        org_id,
        prefix="Open",
        config=OPEN_TO_MEMBERS,
        description="Where launches are planned",
    )
    public = await _create_pod(
        authenticated_client, org_id, prefix="Public", config={"join_policy": "PUBLIC"}
    )
    already_in = await _create_pod(
        authenticated_client, org_id, prefix="Mine", config=OPEN_TO_MEMBERS
    )
    deleted = await _create_pod(
        authenticated_client, org_id, prefix="Gone", config=OPEN_TO_MEMBERS
    )
    gone = await authenticated_client.delete(f"/pods/{deleted['id']}")
    assert gone.status_code == 204, gone.text
    member = await _org_member(authenticated_client, async_client, org_id, "joinable")
    await add_pod_member(
        authenticated_client,
        pod_id=already_in["id"],
        organization_member_id=member["org_member_id"],
        role="POD_USER",
    )

    offered = await _joinable(async_client, org_id, member)

    assert {item["id"] for item in offered["items"]} == {
        open_to_org["id"],
        public["id"],
    }, f"invite-only {invite_only['id']}, joined and deleted pods are not offered"
    assert offered["next_page_token"] is None
    # What someone deciding whether to walk in sees, and nothing more: the
    # pod's one member so far is the admin who made it.
    assert next(i for i in offered["items"] if i["id"] == open_to_org["id"]) == {
        "id": open_to_org["id"],
        "name": open_to_org["name"],
        "description": "Where launches are planned",
        "icon_url": None,
        "join_policy": "ORG_MEMBERS",
        "member_count": 1,
    }

    joined = await async_client.post(
        f"/pods/{open_to_org['id']}/join", headers=auth_headers(member)
    )
    assert joined.status_code == 200, joined.text

    after = await _joinable(async_client, org_id, member)
    assert [item["id"] for item in after["items"]] == [public["id"]]
    assert open_to_org["id"] in await _pods_of(async_client, org_id, member)


async def test_an_owner_is_offered_nothing_and_an_outsider_is_refused(
    authenticated_client: AsyncClient, async_client: AsyncClient, fixed_test_org
):
    org_id = fixed_test_org["id"]
    await _create_pod(
        authenticated_client, org_id, prefix="Open", config=OPEN_TO_MEMBERS
    )

    # An owner opens every pod already, and joining one is refused to them.
    owner_view = await authenticated_client.get(
        f"/organizations/{org_id}/pods/joinable"
    )
    assert owner_view.status_code == 200, owner_view.text
    assert owner_view.json()["items"] == []

    outsider = await signup_user(async_client, "joinable-outsider")
    refused = await async_client.get(
        f"/organizations/{org_id}/pods/joinable", headers=auth_headers(outsider)
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "POD_ACCESS_DENIED"


async def test_joinable_pods_come_a_page_at_a_time(
    authenticated_client: AsyncClient, async_client: AsyncClient, fixed_test_org
):
    org_id = fixed_test_org["id"]
    created = {
        (
            await _create_pod(
                authenticated_client, org_id, prefix="Paged", config=OPEN_TO_MEMBERS
            )
        )["id"]
        for _ in range(3)
    }
    member = await _org_member(authenticated_client, async_client, org_id, "paged")

    first = await _joinable(async_client, org_id, member, limit=2)
    second = await _joinable(
        async_client, org_id, member, limit=2, page_token=first["next_page_token"]
    )

    assert len(first["items"]) == 2
    assert first["next_page_token"] is not None
    assert len(second["items"]) == 1
    assert second["next_page_token"] is None
    assert {item["id"] for item in first["items"] + second["items"]} == created


async def test_an_invite_only_pod_cannot_take_in_new_members(
    authenticated_client: AsyncClient, fixed_test_org
):
    org_id = fixed_test_org["id"]

    refused = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"Closed {uuid4().hex[:8]}",
            "config": {"auto_join_new_members": True},
        },
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "POD_AUTO_JOIN_NEEDS_OPEN_POLICY"

    pod = await _create_pod(
        authenticated_client,
        org_id,
        prefix="Default",
        config={"join_policy": "ORG_MEMBERS", "auto_join_new_members": True},
    )
    assert pod["config"]["auto_join_new_members"] is True

    # Closing it alone would leave it invite-only and taking everyone in.
    closing = await authenticated_client.put(
        f"/pods/{pod['id']}", json={"config": {"join_policy": "INVITE_ONLY"}}
    )
    assert closing.status_code == 422, closing.text
    assert closing.json()["code"] == "POD_AUTO_JOIN_NEEDS_OPEN_POLICY"

    closed = await authenticated_client.put(
        f"/pods/{pod['id']}",
        json={"config": {"join_policy": "INVITE_ONLY", "auto_join_new_members": False}},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["config"]["join_policy"] == "INVITE_ONLY"
    assert closed.json()["config"]["auto_join_new_members"] is False


async def test_a_pod_editor_cannot_make_a_pod_take_in_new_members(
    authenticated_client: AsyncClient, async_client: AsyncClient, fixed_test_org
):
    """Who joins without asking is membership management, as the join policy is."""
    org_id = fixed_test_org["id"]
    pod = await _create_pod(
        authenticated_client, org_id, prefix="Edited", config=OPEN_TO_MEMBERS
    )
    editor = await _org_member(authenticated_client, async_client, org_id, "editor")
    await add_pod_member(
        authenticated_client,
        pod_id=pod["id"],
        organization_member_id=editor["org_member_id"],
        role="POD_EDITOR",
    )

    refused = await async_client.put(
        f"/pods/{pod['id']}",
        headers=auth_headers(editor),
        json={"config": {"auto_join_new_members": True}},
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "INSUFFICIENT_PERMISSION"

    # Still an editor of everything else, and the setting is as it was.
    described = await async_client.put(
        f"/pods/{pod['id']}",
        headers=auth_headers(editor),
        json={"description": "Edited by an editor"},
    )
    assert described.status_code == 200, described.text
    assert described.json()["config"]["auto_join_new_members"] is False


@pytest.mark.drives_work
async def test_someone_who_joins_the_organization_joins_its_default_pods(
    authenticated_client: AsyncClient, async_client: AsyncClient, fixed_test_org
):
    org_id = fixed_test_org["id"]
    # Joins before any pod takes people in: theirs is the run that finds none.
    early = await _org_member(authenticated_client, async_client, org_id, "early")
    await _run_default_pod_joins()

    default_open = await _create_pod(
        authenticated_client,
        org_id,
        prefix="Default",
        config={"join_policy": "ORG_MEMBERS", "auto_join_new_members": True},
    )
    default_public = await _create_pod(
        authenticated_client,
        org_id,
        prefix="Lobby",
        config={"join_policy": "PUBLIC", "auto_join_new_members": True},
    )
    plain_open = await _create_pod(
        authenticated_client, org_id, prefix="Open", config=OPEN_TO_MEMBERS
    )
    await _create_pod(authenticated_client, org_id, prefix="Closed")

    newcomer = await _org_member(authenticated_client, async_client, org_id, "newcomer")
    # Written by the acceptance itself, once for this person in this organization.
    assert f"pod-auto-join:{org_id}:{newcomer['id']}" in await _default_pod_join_items()

    await _run_default_pod_joins()

    assert await _default_pod_join_items() == [], "every join ran to completion"
    assert await _pods_of(async_client, org_id, newcomer) == {
        default_open["id"],
        default_public["id"],
    }
    members = await authenticated_client.get(f"/pods/{default_open['id']}/members")
    assert members.status_code == 200, members.text
    seat = next(m for m in members.json()["items"] if m["user_id"] == newcomer["id"])
    assert seat["roles"] == ["POD_USER"], "the membership a self-join makes"
    # The open pod that does not take people in is still theirs to choose.
    offered = await _joinable(async_client, org_id, newcomer)
    assert [item["id"] for item in offered["items"]] == [plain_open["id"]]
    # Someone already in the organization when the pods were set is not swept in.
    assert await _pods_of(async_client, org_id, early) == set()
