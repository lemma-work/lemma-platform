"""Finding a pod by the names in a link: `/l/{organization handle}/{pod}`.

Against Postgres, because the word a pod's name makes is computed in SQL, and
who may be told which pod a name means turns on rows in two modules: the
organization's join policy and membership, and the pod's policy and members.
The rule itself is held without a database in ``unit/test_pod_addresses.py``.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e

#: A response body as the API sent it: it has no shape until a test asserts one.
Body = dict[str, Any]


async def _create_pod(
    client: AsyncClient, org_id: str, name: str, policy: str | None = None
) -> Body:
    payload: dict[str, object] = {"organization_id": org_id, "name": name}
    if policy is not None:
        payload["config"] = {"join_policy": policy}
    response = await client.post("/pods", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _find(
    client: AsyncClient,
    handle: str,
    word: str,
    user: dict[str, str] | None = None,
) -> str | int:
    """The pod id the name means to ``user`` (else the client's own
    session), or the status that refused it."""
    response = await client.get(
        "/pods/by-name",
        params={"organization": handle, "pod": word},
        headers=auth_headers(user) if user else None,
    )
    if response.status_code != 200:
        return response.status_code
    assert set(response.json()) == {"pod_id"}, response.text
    return response.json()["pod_id"]


async def test_a_name_is_found_only_by_who_could_already_see_or_walk_in(
    authenticated_client: AsyncClient, async_client: AsyncClient, fixed_test_org
):
    org_id = fixed_test_org["id"]
    handle = fixed_test_org["slug"]
    tag = uuid4().hex[:6]
    closed = await _create_pod(authenticated_client, org_id, f"Board {tag}")
    open_to_org = await _create_pod(
        authenticated_client, org_id, f"Sales Team_{tag}", "ORG_MEMBERS"
    )
    public = await _create_pod(authenticated_client, org_id, f"Help {tag}", "PUBLIC")
    member = await signup_user(async_client, "named-member")
    joined = await invite_org_member(
        authenticated_client, async_client, org_id=org_id, user=member
    )
    insider = await signup_user(async_client, "named-insider")
    in_closed = await invite_org_member(
        authenticated_client, async_client, org_id=org_id, user=insider
    )
    await add_pod_member(
        authenticated_client,
        pod_id=closed["id"],
        organization_member_id=in_closed["id"],
        role="POD_USER",
    )
    outsider = await signup_user(async_client, "named-outsider")
    assert joined["id"]

    board, sales, helpdesk = f"board-{tag}", f"sales-team-{tag}", f"help-{tag}"

    # The owner reaches every pod, so is told every name. The word is the
    # web app's: lower case, a hyphen for each run of anything else.
    assert await _find(authenticated_client, handle, board) == closed["id"]
    assert await _find(authenticated_client, handle, sales) == open_to_org["id"]

    # Invite-only: told to who is in it, and to nobody else -- a member of the
    # organization included.
    assert await _find(async_client, handle, board, insider) == closed["id"]
    assert await _find(async_client, handle, board, member) == 404
    assert await _find(async_client, handle, board, outsider) == 404

    # Open to the organization: told to who is in the organization, not to
    # somebody who may not join it.
    assert await _find(async_client, handle, sales, member) == open_to_org["id"]
    assert await _find(async_client, handle, sales, outsider) == 404

    # Open to everyone: told to everyone signed in.
    assert await _find(async_client, handle, helpdesk, outsider) == public["id"]

    # A name nobody has and a handle nobody has are the same answer as a pod
    # the caller may not learn of.
    assert await _find(async_client, handle, f"nothing-{tag}", outsider) == 404
    assert await _find(async_client, f"no-org-{tag}", helpdesk, outsider) == 404


async def test_an_organization_anyone_may_join_opens_its_pods_open_to_it(
    authenticated_client: AsyncClient, async_client: AsyncClient, fixed_test_org
):
    org_id = fixed_test_org["id"]
    handle = fixed_test_org["slug"]
    tag = uuid4().hex[:6]
    open_to_org = await _create_pod(
        authenticated_client, org_id, f"Ops {tag}", "ORG_MEMBERS"
    )
    outsider = await signup_user(async_client, "named-arrival")
    assert await _find(async_client, handle, f"ops-{tag}", outsider) == 404

    opened = await authenticated_client.patch(
        f"/organizations/{org_id}", json={"join_policy": "PUBLIC"}
    )
    assert opened.status_code == 200, opened.text

    # Free to join the organization, they could walk into the pod.
    assert (
        await _find(async_client, handle, f"ops-{tag}", outsider) == open_to_org["id"]
    )


async def test_a_word_two_pods_make_names_neither(
    authenticated_client: AsyncClient, fixed_test_org
):
    org_id = fixed_test_org["id"]
    handle = fixed_test_org["slug"]
    tag = uuid4().hex[:6]
    await _create_pod(authenticated_client, org_id, f"Field Ops {tag}", "PUBLIC")
    await _create_pod(authenticated_client, org_id, f"field-ops_{tag}", "PUBLIC")
    deleted = await _create_pod(authenticated_client, org_id, f"Gone {tag}", "PUBLIC")
    gone = await authenticated_client.delete(f"/pods/{deleted['id']}")
    assert gone.status_code == 204, gone.text

    assert await _find(authenticated_client, handle, f"field-ops-{tag}") == 404
    # A deleted pod's name names nothing.
    assert await _find(authenticated_client, handle, f"gone-{tag}") == 404
