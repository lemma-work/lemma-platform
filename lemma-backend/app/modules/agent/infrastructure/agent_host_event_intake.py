"""Accepting one ordered batch of run events from an Agent Host.

A plain function over a session and a stream, following the same shape as
``agent_host_recovery``: it has one caller, so wrapping it in a class would add
indirection without adding polymorphism.

The rules it enforces are the whole reason at-least-once delivery is safe here.
Everything else about a run is a row write; this path deliberately is not.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host import (
    TERMINAL_AGENT_HOST_RUN_STATES,
    AgentHostEventAck,
    AgentHostEventBatch,
    AgentHostRunState,
)
from app.modules.agent.infrastructure.agent_host_event_stream import (
    AgentHostEventStream,
)
from app.modules.agent.infrastructure.agent_host_repository_common import (
    AgentHostNotFound,
    AgentHostProtocolViolation,
)
from app.modules.agent.infrastructure.runtime_models import AgentHostRunLeaseModel


logger = get_logger(__name__)


async def append_events(
    session: AsyncSession,
    events: AgentHostEventStream,
    *,
    host_id: UUID,
    batch: AgentHostEventBatch,
) -> AgentHostEventAck:
    """Append one ordered batch to the run's stream.

    Deliberately performs no row write. The lease is read under a row lock only
    to serialize concurrent batches for the same run and to validate the epoch;
    the watermark that fences replays lives in the stream, so a chatty run costs
    the database nothing.

    An *empty* stream is treated as a lost stream, not as a run that has emitted
    nothing. The host deletes each event from its own outbox once we ack it, so
    after a Redis flush its oldest surviving event is whatever it had not yet
    sent -- far above sequence 1. Demanding 1 there rejects every batch the host
    has left, forever, and the run produces no output at all. We adopt its
    oldest surviving sequence instead and lose only the events that were already
    gone. A stream that still holds entries keeps strict gap detection, where a
    gap really does mean loss in flight.
    """
    first = batch.events[0]
    lease = await session.get(
        AgentHostRunLeaseModel,
        first.run_id,
        with_for_update=True,
    )
    if lease is None or lease.host_id != host_id:
        raise AgentHostNotFound("run lease does not belong to this host")
    if lease.lease_epoch != first.lease_epoch:
        raise AgentHostProtocolViolation("stale run lease epoch")

    acked_through = await events.last_sequence(run_id=first.run_id)
    expected = acked_through + 1
    terminal = AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES

    stream_was_lost = acked_through == 0
    pending: list[dict] = []
    for event in batch.events:
        # A resend after a lost acknowledgement replays events the stream
        # already holds; first write wins.
        if event.sequence < expected:
            continue
        if terminal:
            raise AgentHostProtocolViolation("terminal run cannot accept events")
        if event.sequence != expected:
            if stream_was_lost and not pending:
                logger.warning(
                    "agent.infrastructure.agent_host_event_intake.stream_resynced",
                    agent_run_id=str(first.run_id),
                    from_sequence=event.sequence,
                )
                expected = event.sequence
            else:
                raise AgentHostProtocolViolation(
                    f"event sequence gap: expected {expected}, got {event.sequence}"
                )
        pending.append(
            {
                "sequence": event.sequence,
                "type": event.type.value,
                "object_id": event.object_id,
                "payload": event.payload,
            }
        )
        expected += 1

    if pending:
        await events.append(run_id=first.run_id, events=pending)
        acked_through = expected - 1

    return AgentHostEventAck(
        run_id=first.run_id,
        lease_epoch=first.lease_epoch,
        acked_through=acked_through,
    )
