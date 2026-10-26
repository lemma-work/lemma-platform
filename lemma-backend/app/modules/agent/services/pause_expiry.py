"""A paused question nobody answered in time: the run is told, and goes on.

An agent's paused ``ask_user`` or ``request_approval`` is a question waiting on
its member (``services.pause_questions``), and a question stops waiting when
its time runs out -- the channels' sweeper expires it. Until the run hears
that, it is paused for ever and the approval is still decidable from the
conversation, which is the one place an expired question must not be decided.

So an expiry is resolved the way every other undecided pause is, through the
one resolution path the conversation's own card uses: ``Pause.expire``, a DENY
recorded for the conversation's member. Never an approval -- nothing runs on
nobody's say-so. The pause ends EXPIRED and its response says why
(``EXPIRED_UNANSWERED``), so the resumed run reads "nobody answered in time"
rather than "the person said no". A question waits 72 hours, an approval 7
days (``domain/pauses``; the question line's ``expires_at`` is the pause's
deadline). Once the decision is recorded the
conversation shows the pause decided; a decision that got there first is
adopted instead.

A question cancelled rather than expired is left alone: it closed because the
pause was decided or moved past, or because its member left the pod -- and a
run is never resumed on behalf of somebody who is no longer in it.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.errors import UnknownApprovalError
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.application.pauses.resolve import Ending, ResolvePause

logger = get_logger(__name__)


async def expire_pause(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    conversation_id: UUID,
    tool_call_id: str,
) -> bool:
    """Resolve the pause on ``tool_call_id`` in the conversation as nobody
    answering it. False when there is nothing to resolve."""
    async with uow_factory() as uow:
        conversation = await Conversations(uow).get_conversation(
            conversation_id, include_runs=False
        )
        if conversation is None or conversation.pod_id != pod_id:
            return False
        context = await create_authorization_data_service(uow).build_user_context(
            user_id=conversation.user_id, pod_id=pod_id
        )
        token = set_current_context(context)
        try:
            await ResolvePause.build(uow).resolve(
                conversation=conversation,
                approval_id=tool_call_id,
                user_id=conversation.user_id,
                pod_id=pod_id,
                decision=AgentRunApprovalDecision.DENY,
                ending=Ending.EXPIRY,
            )
            await uow.commit()
        except UnknownApprovalError:
            return False
        finally:
            reset_current_context(token)
    logger.info(
        "agent.pause_expiry.resolved",
        conversation_id=str(conversation_id),
        tool_call_id=tool_call_id,
    )
    return True


__all__ = ["expire_pause"]
