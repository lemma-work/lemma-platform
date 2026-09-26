"""Messages the person sends while an Agent Host turn is working.

An in-process run hears them through `PendingUserMessagesCapability`, which
claims each one at the next node. An Agent Host run is one ACP
`session/prompt`, which v1 gives no way to add to -- but the Claude Code and
Codex adapters Lemma pins both implement the `_session/steering` extension, and
a harness that advertises it is published with ``steering`` set. For those, the
consume loop hands each new message to the host as a ``STEER_RUN``; the host
sends it into the turn and reports a ``steer_result`` event on the run's own
stream, which is where this module records it.

Only ``delivered`` is a claim. A steer the host could not land -- the turn
ended first, the adapter refused, the run was already gone -- leaves the
message queued, and the follow-up turn that `agent.run.completed` starts
answers it exactly as it answers every message for a harness that cannot steer.
So a lost command, a lost result or a host that predates steering all degrade to
the queue, never to a message nobody answers.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host import AgentHostHarnessCapabilities
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.agent_host.channels import poke_host
from app.modules.agent.infrastructure.agent_host.dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.harnesses.remote_payload import steer_prompt
from app.modules.agent.infrastructure.queued_message_queries import (
    QueuedMessageRepository,
)
from app.modules.agent.services.realtime import (
    message_payload,
    publish_conversation_event,
)
from app.modules.agent.services.serialization import message_to_payload

logger = get_logger(__name__)


class SteerSchedule:
    """When a run next looks for messages to hand its host.

    Never, for a harness that cannot steer: its host would not know the
    command, and the follow-up turn answers those messages instead. Otherwise
    at once, and then every ``interval`` seconds -- tighter than the lease
    check, because this is what a person waits on.
    """

    def __init__(self, *, enabled: bool, interval: float, now: float) -> None:
        self.enabled = enabled
        self.interval = interval
        self._due_at = now

    def due(self, now: float) -> bool:
        if not self.enabled or now < self._due_at:
            return False
        self._due_at = now + self.interval
        return True


def supports_steering(capabilities: JsonObject) -> bool:
    """Whether a harness said it can take a message mid-turn."""
    return AgentHostHarnessCapabilities.model_validate(capabilities).steering


async def forward_queued_messages(
    uow_factory: UnitOfWorkFactory, *, agent_run_id: UUID
) -> int:
    """Send the host anything the person has said since this turn started.

    One indexed ``UPDATE`` that normally matches nothing, which is the whole
    cost on the common path: nobody is typing while the agent works.

    The messages are marked and their commands queued in one transaction, so a
    run that ended in between -- no live lease to queue against -- or one the
    host has not accepted yet rolls the marks back and leaves the messages for
    the next check or the follow-up turn.

    Returns how many were sent. A database error is logged and reported as
    none: the messages stay queued and the follow-up turn still answers them.
    """
    try:
        async with uow_factory() as uow:
            messages = await QueuedMessageRepository(uow).take_queued_messages_to_steer(
                agent_run_id
            )
            if not messages:
                return 0
            dispatch_repository = AgentHostDispatchRepository(uow)
            host_id: UUID | None = None
            for message in messages:
                command = await dispatch_repository.enqueue_steer(
                    run_id=agent_run_id,
                    message_id=message.id,
                    prompt=steer_prompt(message),
                )
                if command is None:
                    # The run is over, or the host has not accepted it yet.
                    # Nothing is committed, so the messages are exactly as
                    # queued as they were: the next check, or the follow-up
                    # turn, takes them.
                    return 0
                host_id = command.host_id
            await uow.commit()
    except SQLAlchemyError:
        logger.warning(
            "agent.harnesses.agent_host.steer_forward_failed.degraded",
            agent_run_id=agent_run_id,
            exc_info=True,
        )
        return 0
    if host_id is not None:
        await poke_host(host_id)
    logger.info(
        "agent.harnesses.agent_host.steer_forwarded.observed",
        agent_run_id=agent_run_id,
        message_count=len(messages),
    )
    return len(messages)


async def record_steer_result(
    uow_factory: UnitOfWorkFactory,
    *,
    agent_run_id: UUID,
    conversation_id: UUID,
    message_id: str | None,
    payload: JsonObject,
) -> None:
    """Write down what the host said about one steer, and show the person.

    The updated message goes out as an ordinary message frame -- the client
    replaces a message by id -- so a message stops reading as queued the moment
    the agent has it, and starts reading as withdrawable again the moment the
    host says it could not deliver it.
    """
    try:
        parsed_id = UUID(str(message_id))
    except ValueError:
        logger.warning(
            "agent.harnesses.agent_host.steer_result_unreadable.degraded",
            agent_run_id=agent_run_id,
        )
        return
    delivered = payload.get("delivered") is True
    detail = payload.get("detail")
    reason = detail if isinstance(detail, str) else None
    try:
        async with uow_factory() as uow:
            message = await QueuedMessageRepository(uow).settle_steer(
                agent_run_id=agent_run_id,
                message_id=parsed_id,
                delivered=delivered,
                detail=reason,
            )
            await uow.commit()
    except SQLAlchemyError:
        # Not worth the run. Unrecorded, a delivered steer is answered a second
        # time by the follow-up turn, which is the failure it had before
        # steering existed; an undelivered one is answered exactly as intended.
        logger.warning(
            "agent.harnesses.agent_host.steer_settle_failed.degraded",
            agent_run_id=agent_run_id,
            exc_info=True,
        )
        return
    logger.info(
        "agent.harnesses.agent_host.steer_settled.observed",
        agent_run_id=agent_run_id,
        delivered=delivered,
        detail=reason,
        matched=message is not None,
    )
    if message is not None:
        await publish_conversation_event(
            conversation_id,
            message_payload(message.agent_run_id, message_to_payload(message)),
        )
