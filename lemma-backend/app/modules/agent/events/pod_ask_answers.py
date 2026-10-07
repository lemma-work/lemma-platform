"""Carrying an answer between pods the moment a run finishes.

A run finishing is the only moment an ask can change hands: in the pod that was
asked it may be the answer, and in the pod that asked it may end a pause that
was holding an answer back. ``PodAskDelivery.settle`` handles both; this only
connects it to ``AgentRunCompletedEvent``, which already arrives inside an
idempotency inbox, and the delivery claim makes a redelivered event a no-op.
"""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.events import AgentRunCompletedEvent
from app.modules.agent.services.pod_ask_delivery import PodAskDelivery

logger = get_logger(__name__)


async def deliver_pod_ask_answers(
    event: AgentRunCompletedEvent, *, uow_factory: UnitOfWorkFactory
) -> None:
    """Deliver what this run's completion owes, absorbing what a retry can't fix.

    A database blip is absorbed for the same reason as in ``subagent_waits``:
    titling and follow-ups ride the same event. A ``DomainError`` -- the asker
    over their usage limit, say -- is absorbed too, since retrying the event
    would meet the same refusal; the answer stays undelivered and is collected
    by the asking conversation's next finished run.
    """
    try:
        await PodAskDelivery(uow_factory).settle(conversation_id=event.conversation_id)
    except SQLAlchemyError, DomainError:
        logger.warning(
            "agent.pod_ask.delivery_failed.degraded",
            conversation_id=str(event.conversation_id),
            agent_run_id=str(event.agent_run_id),
            exc_info=True,
        )
