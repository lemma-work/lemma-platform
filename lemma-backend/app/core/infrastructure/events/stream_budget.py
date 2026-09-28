"""Hold every event stream, together, under one byte budget.

Redis holds the event streams, the job queues, sessions, locks and caches in one
instance with ``noeviction``, so a stream that outgrows memory does not lose
its own data -- it takes every write in the platform down with it. Production
lost login that way on 2026-09-04, and development ran out again on 2026-09-28.

Two earlier bounds each covered half of the problem:

* ``MAXLEN`` counts entries, and entries are not what runs out: the same count
  meant 9MB for one stream and 831MB for another.
* A per-stream byte budget trimmed only entries every consumer group had
  already read. A group whose reader had died pinned that watermark, so the one
  stream that needed trimming was the one it could not touch -- by design,
  with nothing after it.

So this budget is **global** (the streams share one Redis, so they share one
allowance) and **it always holds**. Enforcement goes largest stream first, in
two phases:

1. **Lossless.** Trim each stream to the oldest id every group has delivered.
   Nothing unread is touched; on a healthy platform this is all that ever runs.
2. **Lossy, and recorded.** If that was not enough, trim the largest streams
   through unread entries until the budget holds. Every group that lost
   entries gets a *gap* recorded -- which stream, which group, which window --
   and ``gap_replay`` re-publishes that window from the PostgreSQL outbox once
   the group is reading again. Consumers dedupe through the inbox, so the
   groups that had already read those events are not affected.

Streaq's job queues are counted against the budget but never trimmed: an entry
there is a job, not a notification about one.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field

from redis.exceptions import RedisError

from app.core.log.log import get_logger

logger = get_logger(__name__)

StreamId = tuple[int, int]

#: Consumers named with this suffix only reclaim abandoned deliveries (see
#: ``redis_stream_reclaim_sub``). Their activity says nothing about whether the
#: group's reader is reading -- a reclaimer polling XAUTOCLAIM every minute kept
#: a group with a dead reader looking active.
RECLAIMER_SUFFIX = "-reclaimer"


def field(mapping: object, name: str) -> object:
    """A Redis reply field whose key may arrive as ``str`` or ``bytes``."""
    if not isinstance(mapping, dict):
        return None
    return mapping.get(name, mapping.get(name.encode()))


def text(raw: object) -> str:
    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)


def int_or_none(raw: object) -> int | None:
    """An integer reply, or ``None`` for nil and anything that is not one."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str | bytes):
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def int_field(mapping: object, name: str, default: int = 0) -> int:
    parsed = int_or_none(field(mapping, name))
    return default if parsed is None else parsed


def parse_id(raw: object) -> StreamId | None:
    """A stream id as a comparable pair, or ``None`` if it is not one."""
    candidate = raw.decode() if isinstance(raw, bytes) else raw
    if not isinstance(candidate, str) or "-" not in candidate:
        return None
    left, _, right = candidate.partition("-")
    try:
        return int(left), int(right)
    except ValueError:
        return None


def format_id(stream_id: StreamId) -> str:
    return f"{stream_id[0]}-{stream_id[1]}"


def _first_id(info: object) -> StreamId | None:
    first_entry = field(info, "first-entry")
    if isinstance(first_entry, list | tuple) and first_entry:
        return parse_id(first_entry[0])
    return None


@dataclass(slots=True)
class GroupState:
    name: str
    last_delivered: StreamId | None
    pending: int
    #: ``None`` when Redis cannot say -- which it cannot once entries past the
    #: group's position have been trimmed.
    lag: int | None
    oldest_pending: StreamId | None = None
    #: Milliseconds since the group's reader last read something, ``None`` if
    #: no reader has ever read. Reclaimers excluded; see ``RECLAIMER_SUFFIX``.
    reader_inactive_ms: int | None = None


@dataclass(slots=True)
class StreamState:
    name: str
    length: int
    memory: int
    first_id: StreamId | None
    last_id: StreamId | None
    groups: list[GroupState] = dataclass_field(default_factory=list)
    #: False for job queues, whose entries are work rather than notifications.
    trimmable: bool = True

    def survival_bound(self) -> StreamId | None:
        """The oldest id still in the stream, or the next one if it is empty.

        An emptied stream has no first entry, but it still has a last generated
        id; everything up to and including that id is gone. Without this, a
        trim that removed every entry -- the one that loses the most -- was the
        one that recorded no gap.
        """
        if self.first_id is not None:
            return self.first_id
        if self.last_id is not None:
            return (self.last_id[0], self.last_id[1] + 1)
        return None

    def group_is_behind(self, group: GroupState) -> bool:
        if isinstance(group.lag, int):
            return group.lag > 0
        return group.last_delivered != self.last_id

    def consumed_watermark(self) -> StreamId | None:
        """The oldest id any group still needs; ``None`` if any is unknown.

        A group's oldest *unacked* delivery counts, not just its read position:
        the reclaimer can only hand an entry back while it is still in the
        stream, so trimming at the read position alone deleted exactly the
        messages that had failed once and were waiting for a second try.
        """
        if not self.groups:
            return self.last_id
        marks: list[StreamId] = []
        for group in self.groups:
            mark = group.oldest_pending or group.last_delivered
            if mark is None:
                return None
            marks.append(mark)
        return min(marks)


@dataclass(frozen=True, slots=True)
class StreamGap:
    """Entries a group had not finished with when the budget trimmed them."""

    stream: str
    group: str
    #: Exclusive lower bound: the group had delivered up to here.
    after_ms: int
    #: The first id that survived the trim; everything before it is gone.
    until_ms: int


@dataclass(slots=True)
class BudgetOutcome:
    budget_bytes: int
    total_bytes: int
    reclaimed: dict[str, int] = dataclass_field(default_factory=dict)
    gaps: list[StreamGap] = dataclass_field(default_factory=list)
    #: Still over after both phases: every trimmable stream was already empty.
    still_over: bool = False


async def read_stream_state(client, stream: str, *, trimmable: bool = True):
    """Everything the budget and the stall check need about one stream.

    ``None`` for a stream that does not exist. Raises ``RedisError`` for one
    that exists but cannot be read, so a caller never mistakes "unreadable"
    for "empty".
    """
    if not await client.exists(stream):
        return None
    info = await client.xinfo_stream(stream)
    state = StreamState(
        name=stream,
        length=int_field(info, "length"),
        memory=int(await client.memory_usage(stream) or 0),
        first_id=_first_id(info),
        last_id=parse_id(field(info, "last-generated-id")),
        trimmable=trimmable,
    )
    if not trimmable:
        return state
    for raw_group in await client.xinfo_groups(stream):
        state.groups.append(await _read_group(client, stream, raw_group))
    return state


async def _read_group(client, stream: str, raw_group: object) -> GroupState:
    name = text(field(raw_group, "name") or "")
    delivered = field(raw_group, "last-delivered-id")
    group = GroupState(
        name=name,
        last_delivered=parse_id(
            delivered
            if delivered is not None
            else field(raw_group, "last-delivered-message-id")
        ),
        pending=int_field(raw_group, "pending"),
        lag=int_or_none(field(raw_group, "lag")),
    )
    if group.pending:
        entries = await client.xpending_range(stream, name, min="-", max="+", count=1)
        if entries:
            group.oldest_pending = parse_id(field(entries[0], "message_id"))
    group.reader_inactive_ms = _reader_inactive_ms(
        await client.xinfo_consumers(stream, name)
    )
    return group


def _reader_inactive_ms(consumers: object) -> int | None:
    """How long since any non-reclaimer consumer last read an entry.

    ``inactive`` (Redis 7.2+) counts from the last *successful* read; ``idle``
    also resets on an empty poll, so it is only the fallback. ``-1`` means the
    consumer has never read anything.
    """
    readings = [
        int_or_none(
            field(consumer, "inactive")
            if field(consumer, "inactive") is not None
            else field(consumer, "idle")
        )
        for consumer in (consumers if isinstance(consumers, list) else [])
        if not text(field(consumer, "name") or "").endswith(RECLAIMER_SUFFIX)
    ]
    known = [reading for reading in readings if reading is not None and reading >= 0]
    return min(known) if known else None


def active_readers(consumers: object, *, within_ms: int) -> int:
    """Readers (not reclaimers) that have read something within ``within_ms``."""
    return sum(
        1
        for consumer in (consumers if isinstance(consumers, list) else [])
        if not text(field(consumer, "name") or "").endswith(RECLAIMER_SUFFIX)
        and 0
        <= int_field(
            consumer,
            "inactive",
            default=int_field(consumer, "idle", default=within_ms + 1),
        )
        <= within_ms
    )


def _needed_after(group: GroupState) -> StreamId:
    """The position just before the oldest entry the group still needed.

    Its oldest unacked delivery, or failing that the last entry it read.
    """
    needed_after = group.last_delivered
    if group.oldest_pending is not None:
        before_pending = (group.oldest_pending[0], group.oldest_pending[1] - 1)
        needed_after = (
            before_pending
            if needed_after is None
            else min(needed_after, before_pending)
        )
    return needed_after if needed_after is not None else (0, 0)


def observed_gaps(state: StreamState) -> list[StreamGap]:
    """Groups that have *certainly* lost entries, whatever trimmed them.

    Not only this module trims: an XADD at the hard ceiling does too, and so
    does any operator. Two facts survive a trim and prove a loss. Redis keeps
    ``lag`` as entries-added minus entries-read, which trimming does not change,
    so a lag longer than the whole stream means unread entries are gone. And an
    unacked delivery older than the first surviving entry can never be handed
    back. Only a certain loss counts here: a replay covers minutes of events,
    and a boundary guess made on every pass would replay them over nothing.
    """
    bound = state.survival_bound()
    if bound is None:
        return []
    return [
        StreamGap(
            stream=state.name,
            group=group.name,
            after_ms=_needed_after(group)[0],
            until_ms=bound[0],
        )
        for group in state.groups
        if (isinstance(group.lag, int) and group.lag > state.length)
        or (group.oldest_pending is not None and group.oldest_pending < bound)
    ]


def _groups_losing_entries(
    state: StreamState, new_first: StreamId | None
) -> list[StreamGap]:
    """Which groups a trim to ``new_first`` removed unfinished entries from."""
    if new_first is None:
        return []
    gaps: list[StreamGap] = []
    for group in state.groups:
        needed_after = _needed_after(group)
        # Anything strictly between what it had and what survived is gone.
        if needed_after < (new_first[0], new_first[1] - 1):
            gaps.append(
                StreamGap(
                    stream=state.name,
                    group=group.name,
                    after_ms=needed_after[0],
                    until_ms=new_first[0],
                )
            )
    return gaps


async def _remeasure(client, state: StreamState) -> None:
    info = await client.xinfo_stream(state.name)
    state.first_id = _first_id(info)
    state.length = int_field(info, "length")
    state.memory = int(await client.memory_usage(state.name) or 0)


async def _trim_consumed(client, state: StreamState) -> int:
    """Phase 1: drop what every group has read. Returns bytes reclaimed."""
    watermark = state.consumed_watermark()
    if watermark is None or state.first_id is None or watermark < state.first_id:
        return 0
    before = state.memory
    if not state.groups:
        # Nothing reads it, so nothing in it is unread.
        await client.xtrim(state.name, maxlen=0, approximate=False)
    else:
        # MINID keeps the watermark entry itself; the one after it is the first
        # anything still needs.
        await client.xtrim(state.name, minid=format_id(watermark), approximate=True)
    await _remeasure(client, state)
    return max(0, before - state.memory)


async def _trim_unread(
    client, state: StreamState, excess: int
) -> tuple[int, list[StreamGap]]:
    """Phase 2: trim through unread entries until ``excess`` bytes are gone."""
    if state.length <= 0 or state.memory <= 0:
        return 0, []
    target_bytes = max(0, state.memory - excess)
    target_length = int(state.length * target_bytes / state.memory)
    before = state.memory
    await client.xtrim(state.name, maxlen=target_length, approximate=False)
    await _remeasure(client, state)
    return max(0, before - state.memory), _groups_losing_entries(
        state, state.survival_bound()
    )


async def enforce_stream_budget(
    client, states: list[StreamState], budget_bytes: int
) -> BudgetOutcome:
    """Trim the largest streams until all of them fit ``budget_bytes``.

    ``states`` includes the untrimmable job queues so their bytes are counted.
    Trims are applied in place to ``states``.
    """
    total = sum(state.memory for state in states)
    outcome = BudgetOutcome(budget_bytes=budget_bytes, total_bytes=total)
    if budget_bytes <= 0 or total <= budget_bytes:
        return outcome

    trimmable = [state for state in states if state.trimmable]

    def excess() -> int:
        return sum(state.memory for state in states) - budget_bytes

    for state in sorted(trimmable, key=lambda item: item.memory, reverse=True):
        if excess() <= 0:
            break
        try:
            reclaimed = await _trim_consumed(client, state)
        except RedisError:
            logger.warning(
                "redis.stream.over_budget.degraded",
                stream_name=state.name,
                memory_bytes=state.memory,
                budget_bytes=budget_bytes,
                reason="trim_failed",
                exc_info=True,
            )
            continue
        if reclaimed:
            outcome.reclaimed[state.name] = (
                outcome.reclaimed.get(state.name, 0) + reclaimed
            )

    for state in sorted(trimmable, key=lambda item: item.memory, reverse=True):
        remaining = excess()
        if remaining <= 0:
            break
        try:
            reclaimed, gaps = await _trim_unread(client, state, remaining)
        except RedisError:
            logger.warning(
                "redis.stream.over_budget.degraded",
                stream_name=state.name,
                memory_bytes=state.memory,
                budget_bytes=budget_bytes,
                reason="trim_failed",
                exc_info=True,
            )
            continue
        if reclaimed:
            outcome.reclaimed[state.name] = (
                outcome.reclaimed.get(state.name, 0) + reclaimed
            )
        outcome.gaps.extend(gaps)

    outcome.total_bytes = sum(state.memory for state in states)
    outcome.still_over = outcome.total_bytes > budget_bytes
    return outcome


def publish_entry_cap(state: StreamState, budget_bytes: int) -> int | None:
    """Most entries this stream can hold before it alone exceeds the budget.

    Enforced on every publish (see ``message_bus``), so a burst cannot outrun
    the guard between passes. ``None`` when the average entry size is unknown.
    """
    if budget_bytes <= 0 or state.length <= 0 or state.memory <= 0:
        return None
    average = state.memory / state.length
    return max(1, int(budget_bytes / average))
