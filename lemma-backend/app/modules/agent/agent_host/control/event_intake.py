"""Accepting one ordered batch of run events from an Agent Host.

A plain function over a session and a stream, following the same shape as
``agent_host_recovery``: it has one caller, so wrapping it in a class would add
indirection without adding polymorphism.

The rules it enforces are the whole reason at-least-once delivery is safe here.
Everything else about a run is a row write; this path deliberately is not.
"""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log.log import get_logger
from app.modules.agent.agent_host.domain.host import (
    TERMINAL_AGENT_HOST_RUN_STATES,
    AgentHostEventAck,
    AgentHostEventBatch,
    AgentHostRunState,
)
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.agent_host.control.event_stream import (
    AgentHostEventStream,
)
from app.modules.agent.agent_host.control.repository_common import (
    AgentHostNotFound,
    AgentHostSequenceGap,
    AgentHostStaleLease,
    AgentHostTerminalRun,
)
from app.modules.agent.infrastructure.runtime_models import AgentHostRunLeaseModel


logger = get_logger(__name__)

#: The most one event may put on the run's stream, which is shared Redis. Large
#: enough for an image a chunk carries to the artifact writer -- the same 8 MiB
#: the host's MCP relay allows one line -- and only a ceiling, never a budget.
MAX_EVENT_PAYLOAD_BYTES = 8 * 1024 * 1024


def bounded_event_payload(
    payload: JsonObject, *, limit: int = MAX_EVENT_PAYLOAD_BYTES
) -> JsonObject:
    """``payload``, with its largest fields replaced until it fits ``limit``.

    Replaced field by field rather than refused whole: refusing a batch makes
    the host resend it forever, and dropping the event would leave a gap the
    stream then refuses. The small fields -- a result's ``status``, a call's
    ``tool`` -- survive, so the event still means what it meant.
    """
    if _json_size(payload) <= limit:
        return payload
    bounded: JsonObject = dict(payload)
    for key in sorted(
        bounded, key=lambda name: _json_size(bounded[name]), reverse=True
    ):
        omitted = _json_size(bounded[key])
        bounded[key] = (
            f"[{omitted} bytes omitted: over the {limit}-byte limit for one event]"
        )
        if _json_size(bounded) <= limit:
            break
    return bounded


def _json_size(value: object) -> int:
    return len(json.dumps(value, separators=(",", ":"), default=str))


async def append_events(
    session: AsyncSession,
    events: AgentHostEventStream,
    *,
    host_id: UUID,
    batch: AgentHostEventBatch,
) -> AgentHostEventAck:
    """Append one ordered batch to the run's stream.

    Deliberately performs no row write, and deliberately takes no row lock. The
    lease is read only to answer three questions -- is this host allowed to
    write here, is its epoch current, and has the run already ended -- none of
    which it then mutates. Locking it used to be how concurrent batches for one
    run were serialized, which meant holding a row lock and a pooled database
    connection across two Redis round trips with a 15s socket timeout. That
    ordering guarantee now lives in the stream's own atomic append, next to the
    watermark it is about.

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
    lease = await session.get(AgentHostRunLeaseModel, first.run_id)
    if lease is None or lease.host_id != host_id:
        raise AgentHostNotFound("run lease does not belong to this host")
    if lease.lease_epoch != first.lease_epoch:
        raise AgentHostStaleLease("stale run lease epoch")

    if AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES:
        # A pure replay of what the stream already holds is still tolerated:
        # the host resends until acked, and refusing forever would wedge it.
        watermark = await events.last_sequence(run_id=first.run_id)
        if batch.events[-1].sequence > watermark:
            raise AgentHostTerminalRun("terminal run cannot accept events")
        return AgentHostEventAck(
            run_id=first.run_id,
            lease_epoch=first.lease_epoch,
            acked_through=watermark,
        )

    outcome = await events.append(
        run_id=first.run_id,
        events=[
            {
                "sequence": event.sequence,
                "type": event.type.value,
                "object_id": event.object_id,
                "payload": bounded_event_payload(event.payload),
            }
            for event in batch.events
        ],
    )
    if outcome.gap is not None:
        expected, got = outcome.gap
        raise AgentHostSequenceGap(
            f"event sequence gap: expected {expected}, got {got}"
        )
    if outcome.resynced_from is not None:
        logger.warning(
            "agent.infrastructure.agent_host_event_intake.stream_resynced",
            agent_run_id=str(first.run_id),
            from_sequence=outcome.resynced_from,
        )

    return AgentHostEventAck(
        run_id=first.run_id,
        lease_epoch=first.lease_epoch,
        acked_through=outcome.watermark,
    )
