"""Setting up two pods and a link between them, for the pod-link e2e files.

Everything here goes through the real routes and repositories -- a pod, a table,
a folder, a link -- so a test arranges state the way a person would and asserts
on what the database then lets another pod reach.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import status
from httpx import AsyncClient

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    create_uow_from_session_maker,
)
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
    MessageDraft,
    MessageRole,
)
from app.modules.agent.infrastructure.pod_ask_queries import Teammate
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.services.pod_ask_delivery import PodAskDelivery
from app.modules.agent.services.pod_ask_service import PodAskService
from app.modules.agent.domain.context import ToolContext

READ_TABLE = ["datastore.table.read", "datastore.record.read"]
READ_FOLDER = ["folder.read"]


def uow_factory() -> SessionUnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def ask_service() -> PodAskService:
    return PodAskService(uow_factory())


def delivery() -> PodAskDelivery:
    return PodAskDelivery(uow_factory())


async def add_rows(client: AsyncClient, pod_id: str, table: str, *titles: str) -> None:
    for title in titles:
        made = await client.post(
            f"/pods/{pod_id}/datastore/tables/{table}/records",
            json={"data": {"title": title}},
        )
        assert made.status_code == status.HTTP_201_CREATED, made.text


def table_share(name: str) -> dict[str, object]:
    return {
        "resource_type": "datastore_table",
        "resource_name": name,
        "permission_ids": READ_TABLE,
    }


def folder_share(path: str) -> dict[str, object]:
    return {
        "resource_type": "folder",
        "resource_name": path,
        "permission_ids": READ_FOLDER,
    }


async def connect(
    client: AsyncClient,
    *,
    answering: str,
    asking: str,
    shares: list[dict[str, object]] | None = None,
    headers: dict[str, str] | None = None,
):
    """``PUT /pods/{answering}/pod-links/{asking}``: let ``asking`` ask ``answering``."""
    return await client.put(
        f"/pods/{answering}/pod-links/{asking}",
        json={"shares": shares or []},
        headers=headers,
    )


async def task_conversation(client: AsyncClient, pod_id: str) -> UUID:
    """A conversation nobody typed in, the way a schedule's or a task's is."""
    made = await client.post(
        f"/pods/{pod_id}/conversations", json={"title": "nightly", "type": "TASK"}
    )
    assert made.status_code == status.HTTP_201_CREATED, made.text
    return UUID(made.json()["id"])


def asking_deps(
    *, user_id: UUID, org_id: UUID, pod_id: str, conversation_id: UUID, **extra
) -> ToolContext:
    return ToolContext(
        user_id=user_id,
        org_id=org_id,
        pod_id=UUID(pod_id),
        conversation_id=conversation_id,
        is_pod_default_agent=True,
        **extra,
    )


async def teammate(deps: ToolContext, pod_id: str) -> Teammate:
    teammates = await ask_service().teammates(
        user_id=deps.user_id, organization_id=deps.org_id, pod_id=deps.pod_id
    )
    return next(one for one in teammates if str(one.pod_id) == pod_id)


async def thread(conversation_id: UUID) -> Conversation:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        found = await Conversations(uow).get_conversation(
            conversation_id, include_runs=True
        )
    assert found is not None
    return found


async def finish_latest_run(
    conversation_id: UUID,
    *,
    words: str | None,
    status: AgentRunStatus = AgentRunStatus.COMPLETED,
    error: str | None = None,
) -> UUID:
    """End the conversation's latest run as a run would: having said ``words``."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        conversations = Conversations(uow)
        messages = Messages(uow)
        runs = Runs(uow)
        conversation = await conversations.get_conversation(
            conversation_id, include_runs=True
        )
        assert conversation is not None
        run = conversation.agent_runs[-1]
        if words is not None:
            await messages.append_message(
                conversation_id=conversation_id,
                agent_run_id=run.id,
                draft=MessageDraft.of_text(words, role=MessageRole.ASSISTANT),
            )
        await runs.finish_agent_run(
            agent_run_id=run.id,
            status=status,
            conversation_status=(
                ConversationStatus.COMPLETED
                if status is AgentRunStatus.COMPLETED
                else ConversationStatus.FAILED
            ),
            error=error,
        )
        await uow.commit()
    return run.id


async def messages(conversation_id: UUID):
    async with create_uow_from_session_maker(async_session_maker) as uow:
        found, _ = await Messages(uow).list_messages(
            conversation_id=conversation_id, limit=100
        )
    return found
