"""A parent conversation with children that have all just finished.

Built straight on the repositories, as the finalize of each run would have left
them: a parent whose spawning run has ended, and children whose runs completed
with an answer and owe it to the parent. Each finish then stages its own
follow-ups in its own transaction, as ``RunFinalizer.finish`` does -- so what
is left queued is exactly what a parent and its children finishing together
write.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.subagent_results import (
    PARENT_RUN_KEY,
    REPORTS_TO_PARENT_KEY,
    SUBAGENTS_OUT_KEY,
)
from app.modules.agent.domain.value_objects import (
    AgentRunFinishResult,
    AgentRunStatus,
    AgentRuntimeConfig,
    ConversationStatus,
    MessageDraft,
)
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.services.run_followups import stage_run_followups
from app.modules.agent.tests.e2e.turn_work_support import TurnPod

_RUNTIME = AgentRuntimeConfig(profile_id="system:lemma")


async def _finished_run(
    uow: SqlAlchemyUnitOfWork, conversation: Conversation, answer: str
) -> UUID:
    run = await Runs(uow).create_agent_run(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        agent_runtime=_RUNTIME,
        metadata={},
    )
    await Messages(uow).append_message(
        conversation_id=conversation.id,
        agent_run_id=run.id,
        draft=MessageDraft.of_text(answer),
    )
    await Runs(uow).finish_agent_run(
        agent_run_id=run.id,
        status=AgentRunStatus.COMPLETED,
        output_data={"answer": answer},
    )
    return run.id


async def finished_children(pod: TurnPod, *, count: int) -> tuple[str, list[str]]:
    """A parent and ``count`` children, every run finished, nothing delivered."""
    parent_id = UUID(await pod.conversation([]))
    finishes: list[tuple[UUID, UUID]] = []
    children: list[str] = []
    async with create_uow_from_session_maker(async_session_maker) as uow:
        conversations = Conversations(uow)
        parent = await conversations.get_conversation(parent_id)
        assert parent is not None
        parent_run = await _finished_run(uow, parent, "spawned them all")
        await conversations.set_conversation_metadata_key(
            parent.id, SUBAGENTS_OUT_KEY, True
        )
        for n in range(count):
            child = await conversations.create_conversation(
                Conversation(
                    user_id=parent.user_id,
                    pod_id=parent.pod_id,
                    organization_id=parent.organization_id,
                    agent_id=parent.agent_id,
                    parent_id=parent.id,
                    title=f"child {n}",
                    metadata={
                        "is_sub_agent": True,
                        REPORTS_TO_PARENT_KEY: True,
                        PARENT_RUN_KEY: str(parent_run),
                    },
                )
            )
            finishes.append((child.id, await _finished_run(uow, child, f"result {n}")))
            children.append(str(child.id))
        finishes.append((parent.id, parent_run))
    finished = AgentRunFinishResult(
        status=AgentRunStatus.COMPLETED,
        conversation_status=ConversationStatus.COMPLETED,
        updated=True,
    )
    for conversation_id, run_id in finishes:
        async with create_uow_from_session_maker(async_session_maker) as uow:
            await stage_run_followups(
                uow, run_id=run_id, conversation_id=conversation_id, finish=finished
            )
    return str(parent_id), children


__all__ = ["finished_children"]
