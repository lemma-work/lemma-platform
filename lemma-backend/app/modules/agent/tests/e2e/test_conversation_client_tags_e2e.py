"""A conversation's metadata is its client's tags, and nothing the server decides on.

Before migration 0065, the keys that decided what a conversation was -- a
sub-agent (and so which tools its runs get), a workflow's step (and so who
waits on it), the directory its agent works in -- were metadata keys a client
could PATCH. They are columns now, which no request reaches. A client sending
them keeps them as tags, and they change nothing about how the conversation
behaves; nor do the keys a chat surface's binding was stored under, which is
agent_surfaces' own record now.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.run_origin import run_origin
from app.modules.agent.domain.value_objects import ConversationOrigin
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.tests.e2e.test_agent_e2e import _create_test_pod
from app.modules.agent.domain.toolsets import is_sub_agent_run
from app.modules.agent_surfaces.contracts.conversations import conversation_surface
from app.modules.test_support.conversation_surfaces_double import record_place

pytestmark = [pytest.mark.e2e]

_FORGED = {"is_sub_agent": True, "workflow_run_id": str(uuid4()), "cwd": "/x"}


async def _stored(conversation_id: str) -> Conversation:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        found = await Conversations(uow).get_conversation(UUID(conversation_id))
    assert found is not None
    return found


def _behaves_as_a_chat(conversation: Conversation, *, cwd: str) -> None:
    assert conversation.origin is ConversationOrigin.CHAT
    assert conversation.workflow_run_id is None
    assert conversation.cwd == cwd
    assert run_origin(conversation).origin == "chat"
    assert not is_sub_agent_run(conversation)


@pytest.mark.asyncio
async def test_a_client_patch_of_the_old_keys_changes_nothing(
    authenticated_client, fixed_test_org
):
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    created = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"title": "mine", "metadata": {"lemma_resource": "table:invoices"}},
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]
    cwd = created.json()["workspace_cwd"]

    patched = await authenticated_client.patch(
        f"/pods/{pod_id}/conversations/{conversation_id}",
        json={"metadata": {**_FORGED, "lemma_resource": "table:orders"}},
    )

    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["workspace_cwd"] == cwd
    # Shown as what the row says, not what the client wrote.
    assert body["metadata"]["cwd"] == cwd
    # The client's own tags are still theirs to write.
    assert body["metadata"]["lemma_resource"] == "table:orders"
    _behaves_as_a_chat(await _stored(conversation_id), cwd=cwd)


@pytest.mark.asyncio
async def test_a_client_cannot_open_a_conversation_as_one_either(
    authenticated_client, fixed_test_org
):
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

    created = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"title": "forged", "metadata": {**_FORGED, "source": "SCHEDULE"}},
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["workspace_cwd"] != "/x"
    _behaves_as_a_chat(await _stored(body["id"]), cwd=body["workspace_cwd"])


@pytest.mark.asyncio
async def test_a_surface_binding_is_not_a_clients_to_write(
    authenticated_client, fixed_test_org
):
    """Where a run's answer is posted is agent_surfaces' own record: a client
    sending the keys it was once stored under only tags the conversation, and
    is shown the record over its tag."""
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    created = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"title": "bound", "metadata": {"surface_platform": "SLACK"}},
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]
    async with create_uow_from_session_maker(async_session_maker) as uow:
        assert await conversation_surface(uow, UUID(conversation_id)) is None
        await record_place(uow, UUID(conversation_id), {"surface_platform": "TELEGRAM"})
        await uow.commit()

    patched = await authenticated_client.patch(
        f"/pods/{pod_id}/conversations/{conversation_id}",
        json={"metadata": {"surface_platform": "SLACK", "pinned": True}},
    )

    assert patched.status_code == 200, patched.text
    assert patched.json()["metadata"]["surface_platform"] == "TELEGRAM"
    assert patched.json()["metadata"]["pinned"] is True
    async with create_uow_from_session_maker(async_session_maker) as uow:
        place = await conversation_surface(uow, UUID(conversation_id))
    assert place is not None and place.surface_platform == "TELEGRAM"
