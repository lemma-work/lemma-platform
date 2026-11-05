"""E2E for PROJECT conversations + cwd inheritance.

A PROJECT is a pinned, usually-empty conversation that acts as a group: it owns a
workspace cwd, and conversations created with ``parent_id`` pointing at it inherit
that cwd (sharing the project's directory). Listing stays parent-scoped: roots by
default, children via ``parent_id``; ``type`` filters compose with it.

Pure CRUD — no real model needed, so this is NOT behind LEMMA_RUN_PROVIDER_E2E.
"""

from __future__ import annotations

import pytest

from app.modules.agent.tests.e2e.test_agent_e2e import _create_test_pod
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)
from sandbox_runtime.paths import WORKSPACE_ROOT

pytestmark = [pytest.mark.e2e]


async def _create_conversation(client, pod_id, body):
    response = await client.post(f"/pods/{pod_id}/conversations", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _list_ids(client, pod_id, **params):
    response = await client.get(f"/pods/{pod_id}/conversations", params=params)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


@pytest.mark.asyncio
async def test_project_groups_children_and_shares_cwd(
    authenticated_client, fixed_test_org
):
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

    # A PROJECT gets a directory of its own. The type is preserved (not coerced).
    # A cwd a client sends is a tag, not where the project works.
    project = await _create_conversation(
        authenticated_client,
        pod_id,
        {
            "title": "My Project",
            "type": "PROJECT",
            "metadata": {"cwd": f"{WORKSPACE_ROOT}/projects/alpha"},
        },
    )
    assert project["type"] == "PROJECT"
    project_cwd = project["workspace_cwd"]
    assert project_cwd.startswith(f"{WORKSPACE_ROOT}/c/")
    assert project["metadata"]["cwd"] == project_cwd

    # A PROJECT is a root conversation: it shows in the default list and under a
    # type=PROJECT filter.
    assert project["id"] in await _list_ids(authenticated_client, pod_id)
    assert project["id"] in await _list_ids(
        authenticated_client, pod_id, type="PROJECT"
    )

    # A conversation pinned under the project inherits the project's cwd instead of
    # getting its own c/{date}/{slug} directory.
    child = await _create_conversation(
        authenticated_client,
        pod_id,
        {"title": "task under project", "type": "CHAT", "parent_id": project["id"]},
    )
    assert child["parent_id"] == project["id"]
    assert child["workspace_cwd"] == project_cwd
    # It is a normal conversation, not a spawned sub-agent.
    assert not child["metadata"].get("is_sub_agent")

    # List behavior: children are hidden from the default (root) list and the
    # type=PROJECT filter, and returned via parent_id.
    default_ids = await _list_ids(authenticated_client, pod_id)
    assert child["id"] not in default_ids
    child_ids = await _list_ids(authenticated_client, pod_id, parent_id=project["id"])
    assert child["id"] in child_ids


@pytest.mark.asyncio
async def test_root_conversation_records_own_cwd(authenticated_client, fixed_test_org):
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    convo = await _create_conversation(
        authenticated_client, pod_id, {"title": "root", "type": "CHAT"}
    )
    # cwd is always recorded; a root gets its own c/{date}/{slug} dir, sharing
    # its suffix with the pod filesystem default (`/me/{suffix}`).
    cwd = convo["workspace_cwd"]
    assert cwd.startswith(f"{WORKSPACE_ROOT}/c/")
    # Counted relative to the root, so this says "c/{date}/{slug}" however
    # deep the root itself is.
    assert cwd.removeprefix(f"{WORKSPACE_ROOT}/").count("/") == 2, cwd


@pytest.mark.asyncio
async def test_a_conversation_cannot_be_filed_under_someone_elses(
    authenticated_client, async_client, fixed_test_org
):
    """A child inherits its parent's directory and is listed under it, so a
    parent must be the caller's own conversation in the same pod."""
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    owners_project = await _create_conversation(
        authenticated_client, pod_id, {"title": "Owner's", "type": "PROJECT"}
    )
    private_cwd = owners_project["workspace_cwd"]
    teammate = await signup_user(async_client, "foreign-parent")
    member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=teammate,
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=member["id"],
        role="POD_EDITOR",
    )

    foreign = await async_client.post(
        f"/pods/{pod_id}/conversations",
        json={"title": "mine", "parent_id": owners_project["id"]},
        headers=auth_headers(teammate),
    )
    assert foreign.status_code == 404, foreign.text
    assert private_cwd not in foreign.text

    other_pod = await _create_test_pod(authenticated_client, fixed_test_org)
    cross_pod = await authenticated_client.post(
        f"/pods/{other_pod}/conversations",
        json={"title": "elsewhere", "parent_id": owners_project["id"]},
    )
    assert cross_pod.status_code == 404, cross_pod.text
