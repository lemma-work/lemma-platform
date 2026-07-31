"""Per-run Redis Stream carrying Agent Host run events.

One ordered lane replaces what used to be two. Previously cosmetic text chunks
travelled a lossy pub/sub channel while every other event type was journaled in
PostgreSQL and polled once a second; because the lossy lane wrote into the same
accumulated transcript state as the durable one, the consumer needed sequence
fences and sealed-length bookkeeping to survive out-of-order delivery.

A single stream removes that class of problem outright: entries are ordered and
replayable by construction, so the consumer applies them in order and never has
to reconcile two sources. It also keeps a chatty run's write volume off the
main database entirely.

Durability: the stream is transient transport, not history. Final messages,
artifacts, and usage persist in Lemma's own run storage exactly as before. If
Redis loses the stream the host simply resends from its own local journal --
the ack watermark is the stream's last entry, so a resend is deduplicated by
sequence rather than double-applied.

The key is per-run and therefore dynamic, so it deliberately does not use the
core message-bus consumer-group machinery (``redis_stream_sub`` /
``ensure_consumer_groups``), which is built for a small static set of stream
names. There is exactly one reader per run, so no consumer group is needed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from redis.exceptions import RedisError

from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import JsonObject


logger = get_logger(__name__)

# Entries are written under one field as JSON rather than flattened into hash
# fields, so payload shape is the domain's concern and not the transport's.
_PAYLOAD_FIELD = "event"

# A run that never terminalizes must not leak its stream. This is a backstop
# well beyond any run deadline; the normal path deletes the stream when the run
# reaches a terminal state.
_STREAM_TTL_SECONDS = 24 * 60 * 60

_START_ID = "0-0"

def run_events_stream_key(run_id: UUID) -> str:
    return f"agent-host:run:{run_id}:events"


@dataclass(frozen=True, slots=True)
class StreamedEvent:
    """One event read back from the stream."""

    stream_id: str
    sequence: int
    type: str
    object_id: str | None
    payload: JsonObject


class AgentHostEventStream:
    """Append and tail one run's events."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url

    def _client(self):
        """The process-wide pooled client; disposal is the lifespan's job."""
        return get_redis(url=self._redis_url)

    async def append(
        self,
        *,
        run_id: UUID,
        events: list[JsonObject],
    ) -> None:
        """Append events in order. Callers pass already-validated payloads."""
        if not events:
            return
        redis = self._client()
        key = run_events_stream_key(run_id)
        async with redis.pipeline(transaction=False) as pipe:
            for event in events:
                pipe.xadd(key, {_PAYLOAD_FIELD: json.dumps(event)})
            pipe.expire(key, _STREAM_TTL_SECONDS)
            await pipe.execute()

    async def read(
        self,
        *,
        run_id: UUID,
        after_id: str = _START_ID,
        block_ms: int = 1000,
        count: int = 256,
    ) -> list[StreamedEvent]:
        """Read entries after ``after_id``, blocking briefly when idle.

        Returns an empty list on timeout so the caller keeps control of its own
        deadlines rather than blocking indefinitely here.
        """
        redis = self._client()
        key = run_events_stream_key(run_id)
        try:
            streams = await redis.xread({key: after_id}, count=count, block=block_ms)
        except RedisError:
            logger.debug(
                "agent.infrastructure.agent_host_event_stream.read_failed",
                agent_run_id=str(run_id),
                exc_info=True,
            )
            return []
        if not streams:
            return []

        events: list[StreamedEvent] = []
        for _key, entries in streams:
            for stream_id, fields in entries:
                parsed = _decode(fields)
                if parsed is None:
                    # A malformed entry is dropped rather than stalling the run;
                    # the sequence gap is visible to the consumer.
                    logger.warning(
                        "agent.infrastructure.agent_host_event_stream.entry_dropped",
                        agent_run_id=str(run_id),
                    )
                    continue
                events.append(
                    StreamedEvent(
                        stream_id=stream_id,
                        sequence=_integer(parsed.get("sequence")),
                        type=str(parsed.get("type") or ""),
                        object_id=(
                            str(parsed["object_id"])
                            if parsed.get("object_id") is not None
                            else None
                        ),
                        payload=(
                            dict(parsed["payload"])
                            if isinstance(parsed.get("payload"), dict)
                            else {}
                        ),
                    )
                )
        return events

    async def last_sequence(self, *, run_id: UUID) -> int:
        """Highest sequence currently in the stream, or 0 when empty.

        This is the ack watermark handed back to the host. It lives in the
        stream rather than on the lease row so that acknowledging a batch costs
        no database write.
        """
        redis = self._client()
        key = run_events_stream_key(run_id)
        try:
            entries = await redis.xrevrange(key, count=1)
        except RedisError:
            logger.debug(
                "agent.infrastructure.agent_host_event_stream.watermark_failed",
                agent_run_id=str(run_id),
                exc_info=True,
            )
            return 0
        if not entries:
            return 0
        parsed = _decode(entries[0][1])
        return _integer(parsed.get("sequence")) if parsed else 0

    async def delete(self, *, run_id: UUID) -> None:
        """Drop a terminalized run's stream; best effort."""
        try:
            redis = self._client()
            await redis.delete(run_events_stream_key(run_id))
        except RedisError:
            logger.debug(
                "agent.infrastructure.agent_host_event_stream.delete_failed",
                agent_run_id=str(run_id),
                exc_info=True,
            )


def _decode(fields: dict[Any, Any]) -> JsonObject | None:
    raw = fields.get(_PAYLOAD_FIELD)
    if not isinstance(raw, str):
        return None
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


_default_stream = AgentHostEventStream()


def agent_host_event_stream() -> AgentHostEventStream:
    return _default_stream
