"""Loads what an agent run starts from: its conversation, agent, run and history."""

from __future__ import annotations

from collections.abc import Awaitable
from functools import partial
from typing import Protocol
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.entities import Agent, AgentRun, Conversation, Message
from app.modules.agent.domain.errors import ConversationNotFoundError
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.domain.private_notes import run_is_private
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.history_reads import HistoryReads
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
)
from app.modules.agent.services.channel_background import with_channel_background
from app.modules.agent.services.conversation_access import (
    resolve_agent,
    validate_conversation_access,
)
from app.modules.agent.services.run_phase_spans import record_history_size, run_phase
from app.modules.agent.services.runtime_history import (
    MAX_HISTORY_AGENT_RUNS,
    assemble_runtime_history,
    without_private_runs,
)


async def load_run_context(
    uow_factory: UnitOfWorkFactory,
    *,
    agent_run_id: UUID,
    user_id: UUID,
    pod_id: UUID,
    agent_name: str | None,
) -> tuple[Conversation, Agent, AgentRun, list[Message]]:
    with run_phase("load_context") as span:
        async with uow_factory() as uow:
            conversations = Conversations(uow)
            history_reads = HistoryReads(uow)
            window = await history_reads.load_runtime_history_digests_by_run_id(
                agent_run_id, limit=MAX_HISTORY_AGENT_RUNS
            )
            runs = window.runs
            # In the window or not -- see `RuntimeHistoryWindow`.
            agent_run = window.current_run
            if agent_run is None:
                raise ConversationNotFoundError()
            conversation = validate_conversation_access(
                await conversations.get_conversation(agent_run.conversation_id),
                user_id=user_id,
                pod_id=pod_id,
            )
            agent = await resolve_agent(
                conversation,
                user_id=user_id,
                agent_repository=AgentRepository(uow),
                agent_name=agent_name,
            )
            messages = await assemble_runtime_history(
                history_reads,
                window,
                conversation_id=agent_run.conversation_id,
                run_id=agent_run.id,
                read_channel=partial(with_channel_background, uow),
            )
            if Audience.of(conversation).answers_outsiders and not run_is_private(
                agent_run.metadata
            ):
                messages = without_private_runs(
                    messages, runs, current_run_id=agent_run.id
                )
            record_history_size(span, runs=runs, sent=messages)
            return conversation, agent, agent_run, messages


class RunContextLoader(Protocol):
    """``load_run_context``'s shape, so a runner can be given another."""

    def __call__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        agent_run_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None,
    ) -> Awaitable[tuple[Conversation, Agent, AgentRun, list[Message]]]: ...
