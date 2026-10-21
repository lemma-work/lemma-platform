"""The work a finished run leaves behind, each in its own item and transaction.

Written by ``services/run_followups.stage_run_followups`` in the transaction
that finished the run, so none of them can be lost between the run ending and
somebody acting on it -- the gap the ``agent-events`` consumer group, its inbox
and the "the next message picks it up" comments used to cover.

* ``agent.followup`` -- start the turn for what was said while the run was busy
  (or resume a pause answered before the run finished ending).
* ``agent.subagent_result`` -- a sub-agent's result to a parent that did not
  wait for it, and results a finishing parent left uncollected.
* ``pod_ask.deliver`` -- an answer to the pod that asked, or answers this
  conversation was owed while it could not take them.
* ``agent.replies_arrived`` -- written by ``channel_question_work`` once the last
  question of a batch a conversation asked closes: its next turn.

Each locks only the conversation it starts a turn in, in its own transaction,
so a child's finalize and its parent's never wait on each other. Each is safe to
run twice: the services claim what they deliver (a CAS on the child run's
metadata, the ask's delivery claim, the queued messages' claim into the new
run), so a redelivered item finds nothing left to do.

JOBs rather than FAST: the services they call build usage services and publish
live frames, which are not the database. Their writes still commit in one
transaction each; the item is completed after.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.scope import context_scope
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.work import (
    Exponential,
    JobContext,
    PermanentFailure,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.agent.events.turn_payloads import ConversationWork, RepliesArrived
from app.modules.agent.events.work_kinds import (
    AGENT_FOLLOWUP,
    AGENT_REPLIES_ARRIVED,
    AGENT_SUBAGENT_RESULT,
    POD_ASK_DELIVER,
)

logger = get_logger(__name__)

#: A blip retries quickly; anything that fails five times is a bug to look at
#: in the dead items, not a delivery to keep hammering.
FOLLOWUP_RETRY = RetryPolicy(
    max_attempts=5,
    max_lease_losses=3,
    unavailable_budget=timedelta(hours=1),
    backoff=Exponential(
        base=timedelta(seconds=2), cap=timedelta(minutes=2), jitter=0.2
    ),
)


def _uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


async def start_queued_followup_for(
    conversation_id: UUID,
    completed_run_id: UUID,
    *,
    uow_factory: UnitOfWorkFactory | None = None,
) -> UUID | None:
    """Start the turn owed for what was said while ``completed_run_id`` ran.

    Returns the new run's id, or None when nothing was owed (the common case:
    one indexed count). Raises ``DomainError`` when the turn may not start --
    the account over its limit, say.
    """
    from app.modules.agent.infrastructure.repositories import (
        AgentRepository,
        ConversationRepository,
    )
    from app.modules.agent.services.conversation_service import ConversationService
    from app.modules.agent.services.realtime import (
        message_payload,
        publish_conversation_event,
    )
    from app.modules.agent.services.serialization import message_to_payload
    from app.modules.usage.contracts.execution import build_usage_service

    async with (uow_factory or _uow_factory())() as uow:
        conversations = ConversationRepository(uow)
        conversation = await conversations.get_conversation(conversation_id)
        if conversation is None:
            return None
        # The run acts for the conversation's owner, as every run of it does.
        auth_ctx = await create_authorization_data_service(uow).build_user_context(
            user_id=conversation.user_id, pod_id=conversation.pod_id
        )
        async with context_scope(auth_ctx):
            started = await ConversationService(
                uow=uow,
                conversation_repository=conversations,
                agent_repository=AgentRepository(uow),
                usage_service=build_usage_service(uow),
            ).turns.start_queued_followup(
                conversation=conversation, completed_run_id=completed_run_id
            )
    if started is None:
        return None
    followup_run_id, owed_frames = started
    # Outside the unit of work: these are Redis round trips.
    for message in owed_frames:
        await publish_conversation_event(
            conversation_id,
            message_payload(message.agent_run_id, message_to_payload(message)),
        )
    return followup_run_id


async def start_followup(ctx: JobContext, payload: ConversationWork) -> None:
    """Answer what was said while the run in ``payload`` was busy."""
    assert payload.run_id is not None
    ctx.raise_if_stopping()
    try:
        await start_queued_followup_for(payload.conversation_id, payload.run_id)
    except DomainError as exc:
        # Over a usage limit, say: the same refusal on every retry. The
        # messages stay in the conversation and the next turn answers them.
        logger.warning(
            "agent.queued_followup.refused.degraded",
            conversation_id=str(payload.conversation_id),
            agent_run_id=str(payload.run_id),
            exc_info=True,
        )
        raise PermanentFailure(exc.message) from exc


async def deliver_subagent_result(ctx: JobContext, payload: ConversationWork) -> None:
    from app.modules.agent.services.subagent_result_delivery import (
        SubagentResultDelivery,
    )

    ctx.raise_if_stopping()
    try:
        await SubagentResultDelivery(_uow_factory()).settle(
            conversation_id=payload.conversation_id
        )
    except DomainError as exc:
        # The parent's owner over their limit: the claim rolled back with the
        # delivery, so the result stays owed and the parent's next finish
        # collects it.
        raise PermanentFailure(exc.message) from exc


async def deliver_pod_ask(ctx: JobContext, payload: ConversationWork) -> None:
    from app.modules.agent.services.pod_ask_delivery import PodAskDelivery

    ctx.raise_if_stopping()
    try:
        await PodAskDelivery(_uow_factory()).settle(
            conversation_id=payload.conversation_id
        )
    except DomainError as exc:
        # Collected by the asking conversation's next finished run.
        raise PermanentFailure(exc.message) from exc


async def deliver_replies(ctx: JobContext, payload: RepliesArrived) -> None:
    """Bring a conversation back once everyone it asked has answered."""
    from app.modules.agent.services.message_reply_service import MessageReplyService

    ctx.raise_if_stopping()
    async with _uow_factory()() as uow:
        await MessageReplyService(uow).deliver(
            conversation_id=payload.conversation_id,
            pod_id=payload.pod_id,
            batch_id=payload.batch_id,
        )


def register_run_followup_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=AGENT_FOLLOWUP,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=ConversationWork,
        retry=FOLLOWUP_RETRY,
    )(start_followup)
    registry.handler(
        kind=AGENT_SUBAGENT_RESULT,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=ConversationWork,
        retry=FOLLOWUP_RETRY,
    )(deliver_subagent_result)
    registry.handler(
        kind=POD_ASK_DELIVER,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.JOB,
        payload=ConversationWork,
        retry=FOLLOWUP_RETRY,
    )(deliver_pod_ask)
    registry.handler(
        kind=AGENT_REPLIES_ARRIVED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=RepliesArrived,
        retry=FOLLOWUP_RETRY,
    )(deliver_replies)


register_run_followup_work(default_registry)


__all__ = [
    "FOLLOWUP_RETRY",
    "deliver_pod_ask",
    "deliver_replies",
    "deliver_subagent_result",
    "register_run_followup_work",
    "start_followup",
    "start_queued_followup_for",
]
