"""The fair claim: one short transaction that hands out leases and runs no handler code.

1. Pick namespaces of this class that have due work and room under their cap,
   least recently served first, locking their queue rows (SKIP LOCKED, so two
   dispatchers never pick the same namespace in the same round).
2. Per picked namespace, lock its next candidates in priority order.
3. Walk the candidates and take up to ``min(share, room, free)``, where the
   share is the quantum or, with fewer ready namespaces than free slots, an
   even split of the slots. A keyed
   candidate is taken only while its key has room, counted under a
   transaction-scoped advisory lock on the key -- that lock is what makes
   "count, then take" race-free across dispatchers.
4. A candidate of a rate-limited kind is taken only with a token from each of
   its buckets (``rates.py``), spent in this same transaction. One whose
   bucket is empty is moved to when its token will be there -- not charged,
   not dropped -- and the walk goes on to the next.

Every visited namespace has its ``last_served`` bumped, taken or not, so a
namespace stuck behind a busy key cannot hold the head of the line; a namespace
the round never reached keeps its place for the next round.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.work import rates as rate_buckets
from app.core.infrastructure.work.lifecycle import item_from_row
from app.core.infrastructure.work.rates import (
    BucketId,
    BucketState,
    Rate,
    RateUse,
    bucket_ids,
    rates_from_json,
    wait_for_token,
    wanted_buckets,
)
from app.core.infrastructure.work.types import ClaimedItem, WorkClass

# Readiness is a per-queue probe, LIMIT 1 inside LATERAL, on purpose: written
# as EXISTS, the planner flattens it into a semi-join that reads the whole
# ready backlog of the class (every queued item of every namespace) on every
# claim. As a lateral probe it walks queues in last_served order and stops at
# the LIMIT, so the cost tracks the namespaces visited, not the backlog.
_READY_QUEUES = text(
    """
    SELECT q.ns, q.rates,
           q.max_running - (SELECT count(*) FROM work_items r
                             WHERE r.class = q.class AND r.ns = q.ns
                               AND r.state = 'running') AS room
      FROM work_queues q
     CROSS JOIN LATERAL (
            SELECT 1 FROM work_items w
             WHERE w.class = q.class AND w.ns = q.ns AND w.state = 'queued'
               AND w.run_at <= now() AND w.kind = ANY(CAST(:kinds AS text[]))
             LIMIT 1) AS ready
     WHERE q.class = :cls AND NOT q.paused
       AND (SELECT count(*) FROM work_items r
             WHERE r.class = q.class AND r.ns = q.ns
               AND r.state = 'running') < q.max_running
     ORDER BY q.last_served
     LIMIT :limit
       FOR UPDATE OF q SKIP LOCKED
    """
)
_CANDIDATES = text(
    """
    SELECT id, kind, concurrency_key, concurrency_limit, rate_key
      FROM work_items
     WHERE class = :cls AND ns = :ns AND state = 'queued' AND run_at <= now()
       AND kind = ANY(CAST(:kinds AS text[]))
     ORDER BY priority DESC, run_at
     LIMIT :limit
       FOR UPDATE SKIP LOCKED
    """
)
_TRY_KEY_LOCK = text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))")
_RUNNING_WITH_KEY = text(
    """
    SELECT count(*) FROM work_items
     WHERE concurrency_key = :key AND state = 'running'
    """
)
_MARK_RUNNING = text(
    """
    UPDATE work_items w
       SET state = 'running', lease_owner = :owner,
           lease_epoch = w.lease_epoch + 1,
           lease_until = now() + make_interval(secs => v.lease)
      FROM unnest(CAST(:ids AS uuid[]), CAST(:leases AS float8[])) AS v(id, lease)
     WHERE w.id = v.id
    RETURNING w.id, w.ns, w.class, w.kind, w.payload, w.dedup_key, w.attempts,
              w.lease_losses, w.first_unavailable_at, w.run_at, w.created_at,
              w.lease_epoch
    """
)
_TOUCH_SERVED = text(
    """
    UPDATE work_queues SET last_served = clock_timestamp()
     WHERE class = :cls AND ns = ANY(CAST(:ns AS text[]))
    """
)


@dataclass(frozen=True, slots=True)
class Candidate:
    id: UUID
    kind: str
    concurrency_key: str | None
    concurrency_limit: int | None
    #: Rate buckets this item draws a token from; empty for unlimited kinds.
    buckets: tuple[BucketId, ...] = ()


@dataclass(frozen=True, slots=True)
class ReadyQueue:
    ns: str
    room: int
    rates: Mapping[str, Rate] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Walk:
    taken: list[UUID]
    #: Rate-limited candidates and the seconds until their token is there.
    throttled: dict[UUID, float]
    #: Tokens spent per bucket by what was taken.
    spent: Counter[BucketId]


def take(
    candidates: Sequence[Candidate],
    budget: int,
    key_room: Mapping[str, int],
    buckets: Mapping[BucketId, BucketState] | None = None,
) -> Walk:
    """Walk candidates in order and take up to ``budget`` of them.

    Unkeyed candidates are always takeable. A keyed one is taken while its key
    has room; taking it uses that room up. Skipping a busy key does not stop
    the walk, so keyed work never blocks unkeyed work behind it.

    A rate-limited candidate also needs a token in each of its buckets. With
    one bucket empty it is throttled: the k-th candidate queued behind an
    empty bucket waits until k tokens beyond what is there have refilled. A
    bucket missing from ``buckets`` is held by another claim, so its
    candidates are left for the next round, neither taken nor moved.
    """
    remaining = dict(key_room)
    tokens = {b: state.tokens for b, state in (buckets or {}).items()}
    owed: Counter[BucketId] = Counter()
    walk = Walk(taken=[], throttled={}, spent=Counter())
    for candidate in candidates:
        if len(walk.taken) >= budget:
            break
        key = candidate.concurrency_key
        if key is not None and remaining.get(key, 0) <= 0:
            continue
        if any(b not in tokens for b in candidate.buckets):
            continue
        empty = [b for b in candidate.buckets if tokens[b] < 1]
        if empty:
            owed.update(empty)
            walk.throttled[candidate.id] = max(
                wait_for_token(owed[b] - tokens[b], buckets[b].rate)  # type: ignore[index]
                for b in empty
            )
            continue
        for b in candidate.buckets:
            tokens[b] -= 1
            walk.spent[b] += 1
        if key is not None:
            remaining[key] -= 1
        walk.taken.append(candidate.id)
    return walk


def fair_share(*, quantum: int, free: int, ready: int) -> int:
    """Items each ready namespace may take this round.

    ``quantum`` is the floor, not the ceiling: with fewer ready namespaces than
    free slots, each gets an equal share of the slots, so a namespace alone
    fills every slot it has room for in one round (work-conserving) while
    several split them evenly.
    """
    return max(quantum, free // max(ready, 1))


def namespace_budget(*, share: int, room: int, free: int) -> int:
    return max(0, min(share, room, free))


@dataclass(frozen=True, slots=True)
class ClaimRequest:
    cls: WorkClass
    owner: str
    free: int
    quantum: int
    #: kind -> lease. Only these kinds are claimed: a process never takes work
    #: it has no handler for.
    leases: Mapping[str, timedelta]
    #: kind -> the rate buckets each of its items draws from, for rated kinds.
    rates: Mapping[str, tuple[RateUse, ...]] = field(default_factory=dict)


async def _ready_queues(
    session: AsyncSession, request: ClaimRequest
) -> list[ReadyQueue]:
    rows = await session.execute(
        _READY_QUEUES,
        {
            "cls": request.cls.value,
            "kinds": list(request.leases),
            "limit": request.free,
        },
    )
    return [ReadyQueue(row.ns, row.room, rates_from_json(row.rates)) for row in rows]


async def _candidates(
    session: AsyncSession, request: ClaimRequest, ns: str, budget: int
) -> list[Candidate]:
    # Some headroom past the budget, so candidates behind a busy concurrency
    # key do not leave the namespace's share unused.
    rows = await session.execute(
        _CANDIDATES,
        {
            "cls": request.cls.value,
            "ns": ns,
            "kinds": list(request.leases),
            "limit": budget + request.quantum * 4,
        },
    )
    return [
        Candidate(
            r.id,
            r.kind,
            r.concurrency_key,
            r.concurrency_limit,
            bucket_ids(request.rates.get(r.kind, ()), r.rate_key, r.concurrency_key),
        )
        for r in rows
    ]


async def _key_room(
    session: AsyncSession, candidates: Sequence[Candidate], known: dict[str, int]
) -> dict[str, int]:
    """Room per concurrency key among ``candidates``; 0 when another claim holds the key.

    ``known`` carries keys already resolved in this transaction, already
    reduced by what this round took, so a key shared across namespaces is not
    double-counted.
    """
    for candidate in candidates:
        key = candidate.concurrency_key
        if key is None or key in known:
            continue
        locked = await session.scalar(_TRY_KEY_LOCK, {"key": key})
        if not locked:
            known[key] = 0
            continue
        running = await session.scalar(_RUNNING_WITH_KEY, {"key": key}) or 0
        known[key] = max(0, (candidate.concurrency_limit or 1) - running)
    return known


async def _open_buckets(
    session: AsyncSession, queue: ReadyQueue, candidates: Sequence[Candidate]
) -> dict[BucketId, BucketState]:
    needed = {b for c in candidates for b in c.buckets}
    wanted = wanted_buckets(needed, queue.rates)
    # A name with no rate anywhere (registration refuses one, so only a
    # deployment that dropped a default mid-flight) is unlimited.
    unlimited = {b: BucketState(math.inf, Rate(1, 1.0)) for b in needed - set(wanted)}
    return {**unlimited, **await rate_buckets.open_buckets(session, queue.ns, wanted)}


def _consume_keys(
    candidates: Sequence[Candidate], taken: Sequence[UUID], known: dict[str, int]
) -> None:
    chosen = set(taken)
    for candidate in candidates:
        if candidate.id in chosen and candidate.concurrency_key is not None:
            known[candidate.concurrency_key] -= 1


async def claim(session: AsyncSession, request: ClaimRequest) -> list[ClaimedItem]:
    """Run one claim round in ``session``'s transaction; the caller commits."""
    if request.free <= 0 or not request.leases:
        return []
    queues = await _ready_queues(session, request)
    share = fair_share(quantum=request.quantum, free=request.free, ready=len(queues))
    taken: list[UUID] = []
    kind_of: dict[UUID, str] = {}
    visited: list[str] = []
    known_keys: dict[str, int] = {}
    for queue in queues:
        budget = namespace_budget(
            share=share, room=queue.room, free=request.free - len(taken)
        )
        if budget == 0:
            break
        visited.append(queue.ns)
        candidates = await _candidates(session, request, queue.ns, budget)
        key_room = await _key_room(session, candidates, known_keys)
        buckets = (
            await _open_buckets(session, queue, candidates)
            if any(c.buckets for c in candidates)
            else {}
        )
        walk = take(candidates, budget, key_room, buckets)
        _consume_keys(candidates, walk.taken, known_keys)
        await rate_buckets.spend(
            session,
            queue.ns,
            {b: n for b, n in walk.spent.items() if math.isfinite(buckets[b].tokens)},
        )
        await rate_buckets.throttle(session, walk.throttled)
        kind_of.update((c.id, c.kind) for c in candidates)
        taken.extend(walk.taken)
    if visited:
        await session.execute(_TOUCH_SERVED, {"cls": request.cls.value, "ns": visited})
    if not taken:
        return []
    return await _mark_running(session, request, taken, kind_of)


async def _mark_running(
    session: AsyncSession,
    request: ClaimRequest,
    taken: Sequence[UUID],
    kind_of: Mapping[UUID, str],
) -> list[ClaimedItem]:
    # The lease is per kind; one statement still sets them all, by joining
    # each id to its own lease.
    leases = [request.leases[kind_of[item_id]].total_seconds() for item_id in taken]
    rows = await session.execute(
        _MARK_RUNNING, {"owner": request.owner, "ids": list(taken), "leases": leases}
    )
    return [item_from_row(row) for row in rows]
