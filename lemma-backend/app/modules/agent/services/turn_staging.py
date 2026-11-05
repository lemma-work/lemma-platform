"""Starting an agent turn: the one call every run-start site makes.

A conversation turn starts in the transaction that writes its run and the
message it answers -- from a chat, a surface message, a resumed pause, a retry,
a workflow step. ``stage_agent_turn`` is that call: it writes the ``agent.turn``
item (keyed ``agent-run:<run_id>``, so a stop can cancel it while it is still
queued; one per conversation at a time through ``conversation:<id>``) and, for a
conversation with no title yet, the ``agent.title`` item -- in the caller's
transaction, so a rollback leaves neither and a commit can never leave a run
nobody will execute. That is the hole the outbox -> stream -> streaq hop and the
ten-minute orphan sweep behind it existed to paper over.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkClass, WorkWriter, pod_namespace
from app.modules.agent.contracts.run_facts import AgentRunOrigin
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.run_origin import run_origin
from app.modules.agent.events.turn_payloads import AgentTurnWork, ConversationWork
from app.modules.agent.events.work_kinds import (
    AGENT_TITLE,
    AGENT_TURN,
    AGENT_TURN_AUTOMATION,
    conversation_key,
    title_key,
    turn_key,
)
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.services.run_dispatch import run_enqueue_suppressed

#: Origins whose turns nobody is watching live: they run in the automation
#: class, so a burst of them never queues behind (or ahead of) a person.
_AUTOMATION_ORIGINS: frozenset[AgentRunOrigin] = frozenset(
    {"schedule", "workflow", "trigger"}
)


@dataclass(frozen=True, slots=True)
class AgentTurnStart:
    """The run a turn executes, as the start site already holds it."""

    conversation_id: UUID
    run_id: UUID
    pod_id: UUID
    user_id: UUID
    agent_name: str | None = None
    origin: AgentRunOrigin = "chat"


def turn_for(
    conversation: Conversation,
    *,
    run_id: UUID,
    user_id: UUID,
    agent_name: str | None = None,
) -> AgentTurnStart:
    """The turn of ``run_id`` in ``conversation``, its origin read off the row."""
    origin = run_origin(conversation).origin
    return AgentTurnStart(
        conversation_id=conversation.id,
        run_id=run_id,
        pod_id=conversation.pod_id,
        user_id=user_id,
        agent_name=agent_name,
        origin=origin,
    )


def turn_class_for(origin: AgentRunOrigin) -> WorkClass:
    """The work class a turn started from ``origin`` runs in."""
    if origin in _AUTOMATION_ORIGINS:
        return WorkClass.AUTOMATION
    return WorkClass.INTERACTIVE


def turn_kind_for(cls: WorkClass) -> str:
    """The turn kind registered in ``cls`` (a kind has exactly one class)."""
    return AGENT_TURN_AUTOMATION if cls is WorkClass.AUTOMATION else AGENT_TURN


def _registered() -> None:
    """The kinds must be registered in this process before they are written."""
    import app.modules.agent.events.turn_work  # noqa: F401


async def stage_agent_turn(
    uow: SqlAlchemyUnitOfWork,
    run: AgentTurnStart,
    *,
    cls: WorkClass | None = None,
) -> None:
    """Write ``run``'s turn (and its title, when owed) into ``uow``'s transaction.

    ``cls`` overrides the class ``turn_class_for(run.origin)`` picks. Honours
    ``suppress_agent_run_enqueue`` (a caller running the turn inline).
    """
    if run_enqueue_suppressed():
        return
    _registered()
    work = WorkWriter.of(uow)
    ns = pod_namespace(run.pod_id)
    await work.add(
        turn_kind_for(cls or turn_class_for(run.origin)),
        ns,
        AgentTurnWork(
            run_id=run.run_id,
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            agent_name=run.agent_name,
        ),
        dedup_key=turn_key(run.run_id),
        concurrency_key=conversation_key(run.conversation_id),
    )
    # Only while the conversation has none: one indexed read here instead of
    # a job per turn that finds a title and does nothing. The dedup key only
    # coalesces a *queued* item, so a second turn staged while the first title
    # is still being generated writes another; the concurrency key runs it
    # after the first, which has set the title by then, so it calls no model.
    title = await Conversations(uow).title_of(run.conversation_id)
    if not title:
        await work.add(
            AGENT_TITLE,
            ns,
            ConversationWork(conversation_id=run.conversation_id),
            dedup_key=title_key(run.conversation_id),
            concurrency_key=title_key(run.conversation_id),
        )


__all__ = [
    "AgentTurnStart",
    "stage_agent_turn",
    "turn_class_for",
    "turn_for",
    "turn_kind_for",
]
