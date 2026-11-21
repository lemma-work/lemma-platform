"""E2E: a plugin's block in a shared memory index, against the real datastore.

A plugin writes its block through the file API from a function; the agent's
file tools write the same index whole. The block must survive the agent's
writes, and the plugin must still be able to change and remove it.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.authorization.delegation import is_pod_default_agent
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.pod.models import (
    FileEdit,
    PodEditFileRequest,
    PodReadFileRequest,
    PodWriteFileRequest,
)
from app.modules.agent.infrastructure.tools.pod.pod_file_tools import (
    pod_edit_file,
    pod_read_file,
    pod_write_file,
)

pytestmark = pytest.mark.e2e

BLOCK = "<!-- plugin:tasks -->\n- Tasks is installed.\n<!-- /plugin:tasks -->"
NEW_BLOCK = "<!-- plugin:tasks -->\n- Tasks is installed, v2.\n<!-- /plugin:tasks -->"


async def _create_pod(authenticated_client, fixed_test_org) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"plugin-blocks-{uuid4().hex[:8]}",
            "type": "HYBRID",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    pod_id = response.json()["id"]
    # An agent with memory provisions `/memory`, as it does in a real Lemma.
    response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "keeper",
            "instruction": "Remember things.",
            "toolsets": ["MEMORY", "POD"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return pod_id


async def _plugin_creates(authenticated_client, pod_id: str, folder: str, text: str):
    """The plugin's own first write: the file API, as `pod.files.write_text`."""
    response = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/files",
        data={"directory_path": folder, "search_enabled": "false"},
        files={"data": ("AGENTS.md", text.encode("utf-8"), "text/markdown")},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


async def _plugin_rewrites(authenticated_client, pod_id: str, path: str, text: str):
    response = await authenticated_client.patch(
        f"/pods/{pod_id}/datastore/files/by-path",
        data={"path": path},
        files={"data": ("AGENTS.md", text.encode("utf-8"))},
    )
    assert response.status_code == status.HTTP_200_OK, response.text


def _assistant(user_id: str, pod_id: str) -> ToolContext:
    return ToolContext(
        user_id=UUID(user_id),
        pod_id=UUID(pod_id),
        conversation_id=uuid4(),
        workload_type="agent",
        workload_id=UUID(pod_id),
        agent_name="pod_default",
        is_pod_default_agent=is_pod_default_agent(UUID(pod_id), pod_id=UUID(pod_id)),
    )


async def _text(ctx: ToolContext, path: str) -> str:
    read = await pod_read_file(ctx, PodReadFileRequest(path=path))
    assert read["success"] is True, read
    return read["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("folder", "index"), [("/memory", "/memory/AGENTS.md"), ("/me", "/me/AGENTS.md")]
)
async def test_the_agent_writes_around_a_plugin_block_and_the_plugin_keeps_it(
    authenticated_client, fixed_test_org, fixed_test_user, folder, index
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    ctx = _assistant(fixed_test_user["id"], pod_id)
    await _plugin_creates(
        authenticated_client, pod_id, folder, f"# Memory\n{BLOCK}\n- Clients\n"
    )

    dropped = await pod_write_file(
        ctx, PodWriteFileRequest(path=index, content="# Memory\n- Clients\n")
    )
    edited = await pod_edit_file(
        ctx,
        PodEditFileRequest(
            path=index,
            edits=[FileEdit(old_text="Tasks is installed.", new_text="Tasks.")],
        ),
    )
    assert dropped["success"] is False and "plugin:tasks" in dropped["error"]
    assert edited["success"] is False and "plugin:tasks" in edited["error"]
    assert BLOCK in await _text(ctx, index)

    kept = await pod_write_file(
        ctx,
        PodWriteFileRequest(path=index, content=f"# Memory\n- Billing\n{BLOCK}\n"),
    )
    assert kept["success"] is True, kept

    # The plugin's set-up runs again with a new block, then uninstalls.
    await _plugin_rewrites(
        authenticated_client, pod_id, index, f"# Memory\n- Billing\n{NEW_BLOCK}\n"
    )
    assert NEW_BLOCK in await _text(ctx, index)
    await _plugin_rewrites(authenticated_client, pod_id, index, "# Memory\n- Billing\n")
    assert "plugin:tasks" not in await _text(ctx, index)

    # With the block gone, the agent's notes are its own again.
    rewritten = await pod_write_file(
        ctx, PodWriteFileRequest(path=index, content="# Memory\n- Billing, monthly\n")
    )
    assert rewritten["success"] is True, rewritten
