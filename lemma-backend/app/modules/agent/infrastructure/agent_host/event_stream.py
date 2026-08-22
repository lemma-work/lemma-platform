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
artifacts, and usage persist in Lemma's own run storage exactly as before. The
ack watermark is the stream's last entry, so a resend of events the stream
still holds is deduplicated by sequence rather than double-applied.

Losing the stream is *not* fully recoverable, and the intake path is written
accordingly. The host deletes an event from its local outbox once we ack it, so
after a Redis flush its oldest surviving event is whatever it had not yet sent
-- typically far above sequence 1. ``append_events`` therefore treats an empty
stream as "we lost it" and adopts the host's oldest surviving sequence instead
of demanding 1; the events between are gone. Gap detection stays strict for a
stream that still has entries, where a gap really does mean loss in flight.

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

# The sequence is written as its own stream field as well as inside the payload
# so the append script can read it without decoding JSON. Entries from a build
# that predates it are still readable: the script falls back to the payload.
_SEQUENCE_FIELD = "sequence"

# Append is one atomic read-modify-write, because the watermark it appends
# against lives in the stream it is appending to.
#
# The alternative -- read the watermark, decide in Python, then append -- is
# only safe while something else serializes concurrent batches for a run, and
# what used to serialize them was a PostgreSQL row lock on the lease, held
# across both of these Redis calls. That put a lock and a pooled database
# connection behind a network round trip with a 15s socket timeout, on the one
# path whose whole point is that it writes no rows. Redis executes a script
# atomically, so the ordering guarantee lives with the data it is about.
#
# Returns {status, watermark, expected, got, resynced_from}: status 0 appended
# (or deduped), status 1 found a gap and appended nothing. ``resynced_from`` is
# the sequence a lost stream restarted at, or 0.
_APPEND_SCRIPT = """
local key = KEYS[1]
local ttl = tonumber(ARGV[1])

local last = 0
local newest = redis.call('XREVRANGE', key, '+', '-', 'COUNT', 1)
if #newest > 0 then
  local fields = newest[1][2]
  for index = 1, #fields, 2 do
    if fields[index] == 'sequence' then
      last = tonumber(fields[index + 1]) or 0
    elseif fields[index] == 'event' and last == 0 then
      -- Only entries from a build predating the sequence field reach here, and
      -- an unreadable one must not abort the append: a script error would let a
      -- single poison entry stall the run's whole feed. Unreadable reads as 0,
      -- which is the lost-stream path -- exactly what the Python reader did.
      local ok, decoded = pcall(cjson.decode, fields[index + 1])
      if ok and type(decoded) == 'table' then
        last = tonumber(decoded['sequence']) or 0
      end
    end
  end
end

-- An empty stream means a lost stream, not a run that has emitted nothing:
-- the host drops each event once we ack it, so its oldest surviving event is
-- far above 1. Demanding 1 here would reject everything it has left, forever.
local stream_was_lost = (last == 0)
local expected = last + 1
local appended = 0
local resynced_from = 0

for index = 2, #ARGV, 2 do
  local sequence = tonumber(ARGV[index])
  local payload = ARGV[index + 1]
  -- A resend after a lost acknowledgement replays what we already hold.
  if sequence >= expected then
    if sequence ~= expected then
      if stream_was_lost and appended == 0 then
        expected = sequence
        resynced_from = sequence
      else
        return {1, last, expected, sequence, 0}
      end
    end
    redis.call('XADD', key, '*', 'sequence', sequence, 'event', payload)
    appended = appended + 1
    expected = expected + 1
  end
end

if appended > 0 then
  redis.call('EXPIRE', key, ttl)
end
return {0, expected - 1, 0, 0, resynced_from}
"""

# Every command on this client is bounded so a black-holed Redis cannot hang a
# caller indefinitely. ``append_events`` runs its stream calls while holding a
# row lock on the lease, so an unbounded wait there pins a PostgreSQL lock for
# as long as TCP keepalive takes to notice (tens of minutes).
_SOCKET_TIMEOUT_SECONDS = 15.0
# The blocking read is a normal command as far as the socket is concerned, so
# its server-side block must stay well inside the socket timeout above.
_MAX_BLOCK_MS = 5_000


def run_events_stream_key(run_id: UUID) -> str:
    return f"agent-host:run:{run_id}:events"


@dataclass(frozen=True, slots=True)
class AppendOutcome:
    """What one append decided.

    ``gap`` is ``(expected, got)`` when the batch could not be applied because
    it skipped a sequence the stream never saw — real loss in flight, as
    opposed to a resend of events already held, which is deduplicated silently.
    """

    watermark: int
    gap: tuple[int, int] | None = None
    resynced_from: int | None = None


@dataclass(frozen=True, slots=True)
class StreamedEvent:
    """One event read back from the stream."""

    stream_id: str
    sequence: int
    type: str
    object_id: str | None
    payload: JsonObject


class StreamBatch(list[StreamedEvent]):
    """The events one read produced, plus the id to resume reading from.

    The cursor is deliberately not ``events[-1].stream_id``. An entry that
    cannot be parsed is dropped from the batch but still has to advance the
    cursor: leaving it behind means the next ``XREAD`` returns it again,
    returns nothing after the drop, and the caller's loop spins on it with no
    block and no progress. Tracking the cursor separately is what makes the
    drop a drop rather than a stall.

    It is a list so every consumer can keep iterating the events directly; the
    cursor rides along for the one consumer that also has to remember where it
    got to.
    """

    __slots__ = ("cursor",)

    def __init__(self, events: list[StreamedEvent], *, cursor: str) -> None:
        super().__init__(events)
        self.cursor = cursor


class AgentHostEventStream:
    """Append and tail one run's events."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url

    def _client(self):
        """The process-wide pooled client; disposal is the lifespan's job."""
        return get_redis(
            url=self._redis_url,
            socket_timeout=_SOCKET_TIMEOUT_SECONDS,
        )

    async def append(
        self,
        *,
        run_id: UUID,
        events: list[JsonObject],
    ) -> "AppendOutcome":
        """Append a contiguous batch against the stream's own watermark.

        Deduplication, gap detection and the lost-stream resync all happen
        inside one atomic script, so two batches racing for the same run cannot
        interleave and no caller has to hold a lock to make that true.
        """
        if not events:
            return AppendOutcome(watermark=await self.last_sequence(run_id=run_id))
        redis = self._client()
        arguments: list[object] = [_STREAM_TTL_SECONDS]
        for event in events:
            arguments.append(_integer(event.get("sequence")))
            arguments.append(json.dumps(event))
        status, watermark, expected, got, resynced_from = await redis.eval(
            _APPEND_SCRIPT,
            1,
            run_events_stream_key(run_id),
            *arguments,
        )
        if int(status) != 0:
            return AppendOutcome(
                watermark=int(watermark),
                gap=(int(expected), int(got)),
            )
        return AppendOutcome(
            watermark=int(watermark),
            resynced_from=int(resynced_from) or None,
        )

    async def read(
        self,
        *,
        run_id: UUID,
        after_id: str = _START_ID,
        block_ms: int = 1000,
        count: int = 256,
    ) -> StreamBatch:
        """Read entries after ``after_id``, blocking briefly when idle.

        Returns an empty batch on timeout so the caller keeps control of its own
        deadlines rather than blocking indefinitely here. A Redis failure is
        raised, not swallowed: a caller that cannot read is producing no output
        at all, and silently returning "nothing yet" for the rest of the run
        turns an outage into a run that reports no error and no result.
        """
        redis = self._client()
        key = run_events_stream_key(run_id)
        streams = await redis.xread(
            {key: after_id},
            count=count,
            block=min(block_ms, _MAX_BLOCK_MS),
        )
        cursor = after_id
        if not streams:
            return StreamBatch([], cursor=cursor)

        events: list[StreamedEvent] = []
        for _key, entries in streams:
            for stream_id, fields in entries:
                # Advance past every entry we consumed, including the ones we
                # drop, or the next read starts before the poison entry again.
                cursor = stream_id
                parsed = _decode(fields)
                if parsed is None:
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
        return StreamBatch(events, cursor=cursor)

    async def last_sequence(self, *, run_id: UUID) -> int:
        """Highest sequence currently in the stream, or 0 when empty.

        This is the ack watermark handed back to the host. It lives in the
        stream rather than on the lease row so that acknowledging a batch costs
        no database write.

        A Redis failure raises rather than reading as 0. Zero is load-bearing —
        the intake path reads it as "the stream is empty, adopt whatever the
        host still has" — so reporting it for an unreachable server would let a
        blip rewrite the run's accepted sequence range.
        """
        redis = self._client()
        key = run_events_stream_key(run_id)
        entries = await redis.xrevrange(key, count=1)
        if not entries:
            return 0
        fields = entries[0][1]
        if _SEQUENCE_FIELD in fields:
            return _integer(fields[_SEQUENCE_FIELD])
        parsed = _decode(fields)
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
    except TypeError, ValueError:
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
