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

    # A PROJECT with an explicit workspace cwd. The type is preserved (not coerced).
    project = await _create_conversation(
        authenticated_client,
        pod_id,
        {
            "title": "My Project",
            "type": "PROJECT",
            "metadata": {"cwd": "/workspace/projects/alpha"},
        },
    )
    assert project["type"] == "PROJECT"
    assert project["metadata"]["cwd"] == "/workspace/projects/alpha"

    # A PROJECT is a root conversation: it shows in the default list and under a
    # type=PROJECT filter.
    assert project["id"] in await _list_ids(authenticated_client, pod_id)
    assert project["id"] in await _list_ids(authenticated_client, pod_id, type="PROJECT")

    # A conversation pinned under the project inherits the project's cwd instead of
    # getting its own c/{date}/{slug} directory.
    child = await _create_conversation(
        authenticated_client,
        pod_id,
        {"title": "task under project", "type": "CHAT", "parent_id": project["id"]},
    )
    assert child["parent_id"] == project["id"]
    assert child["metadata"]["cwd"] == "/workspace/projects/alpha"
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
    # cwd is always recorded in metadata; a root gets its own c/{date}/{slug} dir,
    # sharing its suffix with the pod filesystem default (`/me/{suffix}`).
    cwd = convo["metadata"]["cwd"]
    assert cwd.startswith("/workspace/c/")
    assert cwd.count("/") == 4  # /workspace/c/{date}/{slug}
