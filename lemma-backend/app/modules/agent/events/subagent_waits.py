"""Waking a parent run the moment the child it is waiting on finishes.

A SUBAGENT wait is the one kind nobody has to poll for. The child already
publishes ``AgentRunCompletedEvent`` on the agent event stream, already inside
an idempotency inbox, and `workflow` already resumes a suspended *workflow* run
off exactly that event — so the parent conversation gets the same treatment.

The timer underneath stays armed regardless. It is the ceiling and the backstop:
if this event is never delivered the wait still resolves at its deadline, and
the resolver checks the run before deciding, so a missed event costs latency
rather than a stuck conversation.
"""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from app.core.log.log import get_logger
from app.modules.agent.domain.wait import AgentWaitType
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.wait_wake_service import AgentWaitService

logger = get_logger(__name__)


async def resolve_parent_wait_for_finished_child(event, *, uow_factory) -> None:
    """Absorb a database blip here rather than failing the whole event.

    Titling and queued follow-ups run off this same event, and a parent whose
    wait could not be resolved still has its deadline underneath -- so a
    transient failure costs latency, while raising costs a child completion that
    never lands.

    Deliberately `SQLAlchemyError` and not `Exception`: a blip is the failure
    worth absorbing, and anything else here is a bug. Swallowing those is how
    the platform came to believe its own error rate was half what it was, which
    is the thing this release is undoing rather than repeating.
    """
    try:
        await _resolve_parent_wait(event, uow_factory=uow_factory)
    except SQLAlchemyError:
        logger.warning(
            "agent.wait.child_finished_resolve_failed.degraded",
            conversation_id=str(event.conversation_id),
            agent_run_id=str(event.agent_run_id),
            exc_info=True,
        )


async def _resolve_parent_wait(event, *, uow_factory) -> None:
    """Resolve the parent's SUBAGENT wait, if the finished run has one waiting.

    Keyed on the *parent conversation* rather than on the child run id, because
    a conversation has at most one ACTIVE wait by construction — the partial
    unique index says so — and that single row is cheaper to find than a scan
    for a matching external ref.
    """
    async with uow_factory() as uow:
        conversation = await ConversationRepository(uow).get_conversation(
            event.conversation_id, include_runs=False
        )
        if conversation is None or conversation.parent_id is None:
            return

        waits = AgentConversationWaitRepository(uow)
        wait = await waits.find_active_for_conversation(conversation.parent_id)
        if wait is None or wait.wait_type is not AgentWaitType.SUBAGENT:
            return
        # The parent may be waiting on a *different* child. Resolving the wrong
        # one would wake it to a result it never asked for.
        if str((wait.spec or {}).get("target_ref")) != str(event.agent_run_id):
            return

        resolved = await AgentWaitService(uow).resolve(wait=wait)
        await uow.commit()

    if resolved:
        logger.debug(
            "agent.wait.child_finished",
            conversation_id=str(conversation.parent_id),
            wait_id=str(wait.id),
        )
