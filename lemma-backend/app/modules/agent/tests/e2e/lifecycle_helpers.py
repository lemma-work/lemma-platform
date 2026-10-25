"""Putting a conversation into the states the lifecycle races start from.

The races these tests pin down are between things that happen in separate
transactions -- a decision and its return, a run finishing and an answer
landing -- so each state is seeded directly, one committed step at a time, and
the code under test then runs against real Postgres.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select, update
from starlette import status

from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.scope import context_scope
from app.core.authorization.session_approvals import record_denial
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    create_uow_from_session_maker,
)
from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    AgentRuntimeConfig,
    ConversationStatus,
    JsonObject,
    MessageDraft,
    MessageRole,
)
from app.modules.agent.infrastructure.models import AgentRunModel, ConversationModel
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
)
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.agent.tools.approval_denials import show_card
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.usage.contracts.execution import UsageService

SYSTEM_RUNTIME = AgentRuntimeConfig(profile_id="system:lemma")


@dataclass(frozen=True, slots=True)
class Seeded:
    pod_id: UUID
    user_id: UUID
    agent_id: UUID
    agent_name: str
    conversation_id: UUID


def uow_factory() -> SessionUnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


@asynccontextmanager
async def owner_uow(seeded: Seeded) -> AsyncIterator[SqlAlchemyUnitOfWork]:
    """A unit of work acting as the conversation's owner, as a request would."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        ctx = await create_authorization_data_service(uow).build_user_context(
            user_id=seeded.user_id, pod_id=seeded.pod_id
        )
        async with context_scope(ctx):
            yield uow


def conversation_service(
    uow: SqlAlchemyUnitOfWork, *, usage_service: UsageService | None = None
) -> ConversationService:
    return ConversationService(
        uow=uow,
        agent_repository=AgentRepository(uow),
        usage_service=usage_service,
    )


async def seeded_conversation(
    scenario: E2EScenario,
    *,
    toolsets: list[str] | None = None,
    parent_id: UUID | None = None,
) -> Seeded:
    """A pod, an agent on a system runtime, and one conversation with it."""
    if scenario.pod is None:
        await scenario.create_org_with_pod(name_prefix="Lifecycle")
    agent_name = f"lifecycle-{uuid4().hex[:8]}"
    body: JsonObject = {
        "name": agent_name,
        "instruction": "Answer in plain text.",
        "agent_runtime": SYSTEM_RUNTIME.model_dump(mode="json"),
    }
    if toolsets is not None:
        body["toolsets"] = list(toolsets)
    agent = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/agents", json=body
    )
    assert agent.status_code == status.HTTP_201_CREATED, agent.text
    conversation = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations",
        json={"agent_name": agent_name, "title": "Lifecycle", "type": "CHAT"},
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = UUID(conversation.json()["id"])
    if parent_id is not None:
        # The public route does not take a parent; a spawn sets it.
        async with async_session_maker() as session:
            await session.execute(
                update(ConversationModel)
                .where(ConversationModel.id == conversation_id)
                .values(parent_id=parent_id)
            )
            await session.commit()
    return Seeded(
        pod_id=UUID(scenario.pod_id),
        user_id=UUID(scenario.owner_user["id"]),
        agent_id=UUID(agent.json()["id"]),
        agent_name=agent_name,
        conversation_id=conversation_id,
    )


async def running_run(seeded: Seeded, *, metadata: JsonObject | None = None) -> UUID:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        run = await Runs(uow).create_agent_run(
            conversation_id=seeded.conversation_id,
            agent_id=seeded.agent_id,
            agent_runtime=SYSTEM_RUNTIME,
            metadata=metadata or {"source": "e2e_lifecycle"},
        )
        await uow.commit()
    return run.id


async def append(seeded: Seeded, run_id: UUID | None, draft: MessageDraft) -> Message:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        saved = await Messages(uow).append_message(
            conversation_id=seeded.conversation_id, agent_run_id=run_id, draft=draft
        )
        await uow.commit()
    return saved


async def pausing_call(
    seeded: Seeded,
    run_id: UUID,
    *,
    tool_name: str,
    tool_args: JsonObject,
    metadata: JsonObject | None = None,
) -> str:
    tool_call_id = f"call-{uuid4().hex[:12]}"
    await append(
        seeded,
        run_id,
        MessageDraft.of_tool_call(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_args=tool_args,
            metadata=metadata,
        ),
    )
    inner = tool_args.get("tool_name")
    if tool_name == "request_approval" and isinstance(inner, str):
        # As the tool does when it pauses: what is pending now is written on
        # the card, and is all an answer to it lends.
        inner_args = tool_args.get("args")
        await show_card(
            conversation_id=seeded.conversation_id,
            approval_id=tool_call_id,
            person_user_id=seeded.user_id,
            workload_actor_id=f"agent:{seeded.agent_id}",
            tool_name=inner,
            args=inner_args if isinstance(inner_args, dict) else {},
        )
    return tool_call_id


async def queued_message(seeded: Seeded, run_id: UUID, text: str) -> Message:
    """A message the person sent while ``run_id`` was going."""
    return await append(
        seeded,
        run_id,
        MessageDraft.of_text(
            text, role=MessageRole.USER, metadata={"during_active_run": True}
        ),
    )


async def finish(
    run_id: UUID,
    *,
    status: AgentRunStatus = AgentRunStatus.COMPLETED,
    conversation_status: ConversationStatus | None = None,
) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await Runs(uow).finish_agent_run(
            agent_run_id=run_id,
            status=status,
            conversation_status=conversation_status,
        )
        await uow.commit()


async def runs(conversation_id: UUID) -> list[AgentRunModel]:
    async with async_session_maker() as session:
        result = await session.execute(
            select(AgentRunModel)
            .where(AgentRunModel.conversation_id == conversation_id)
            .order_by(AgentRunModel.created_at, AgentRunModel.id)
        )
        return list(result.scalars())


async def messages(conversation_id: UUID) -> list[Message]:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        found, _ = await Messages(uow).list_messages(
            conversation_id=conversation_id, limit=500
        )
    return found


async def conversation(conversation_id: UUID) -> Conversation:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        loaded = await Conversations(uow).get_conversation(
            conversation_id, include_runs=False
        )
    assert loaded is not None
    return loaded


#: What a pod-tool delete of a record is refused for, one at a time, when the
#: agent holds no grant on the table.
RECORD_DELETE_REFUSALS = ("datastore.table.read", "datastore.record.delete")


async def refused_before_asking(
    seeded: Seeded, *, tool_name: str, args: JsonObject, permission_ids: tuple[str, ...]
) -> None:
    """The refusals an agent hits before it asks: the server's record of them.

    A seeded ``request_approval`` skips the call the agent made first, and an
    approval lends exactly what the server refused that call for -- never the
    person's whole authority, and never the model's own ``permission_ids``. So a
    test that approves a call the agent "was refused" writes the refusal down,
    as the pod tool would have.
    """
    await record_denial(
        session_id=str(seeded.conversation_id),
        workload_actor_id=f"agent:{seeded.agent_id}",
        tool_name=tool_name,
        args=dict(args),
        permission_ids=permission_ids,
    )
