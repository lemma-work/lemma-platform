"""Sending what a schedule's triage held, together, when its digest is due.

One digest is one run: a single `schedule.fired` whose payload is
``{"events": [...the held payloads, oldest first...], "held": N}`` and whose
metadata says it is a digest. A workflow reads the events as
``start.payload.events``; an agent is told in its first message.

A digest takes the held events oldest first, as many as fit in its bounds --
a count, and a size, since the whole digest rides one event -- and the rest
go out in another digest at the next sweep, which the metadata says, until
none are left. An event too large to quote goes in as a stub naming its run,
where the whole of it still is.

The events of a row on an RLS table belong to that row's owner, and a run
carries one person's authority, so a digest is sent per owner: one run each.
On a table without RLS, and for every webhook, that is the schedule's owner
and one run.

Exactly once across replicas, as a TIME occurrence is: the schedule is claimed
with ``FOR UPDATE SKIP LOCKED`` and its cursor advanced, its held rows are
taken the same way, and the digest run, the rows it sends and its
`schedule.fired` all commit in that one transaction.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID

from pydantic import JsonValue

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.schedule.domain.events.schedule import ScheduleFired
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleRunEntity
from app.modules.schedule.domain.triage import DIGEST_EVENT_PREFIX
from app.modules.schedule.repositories.held_runs import (
    DueDigest,
    HeldRunRepository,
    claim_due_digest,
)

#: The most events one digest sends.
DIGEST_MAX_EVENTS = 50
#: The most characters of event JSON one digest carries. A digest is one
#: `schedule.fired` on a stream capped by entry count, and its events are what
#: a woken agent reads first, so it is kept to what one prompt can hold.
DIGEST_MAX_CHARS = 48_000
#: Above this one event goes in as a stub naming its run.
DIGEST_EVENT_MAX_CHARS = 16_000
#: The most digests one sweep sends. The rest are still due on the next one.
DIGEST_CLAIM_LIMIT = 20
#: When a digest that left events behind sends the next batch: just after this
#: sweep, so the next one takes it -- each batch its own occurrence and key.
DIGEST_CONTINUE_AFTER = timedelta(seconds=1)


@dataclass(slots=True)
class DigestBatch:
    """What one digest run sends: one owner's events, in the order they arrived."""

    user_id: UUID
    runs: list[ScheduleRunEntity] = field(default_factory=list)
    events: list[JsonValue] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DigestPlan:
    batches: list[DigestBatch]
    more_waiting: bool


async def dispatch_due_digests(
    uow_factory: UnitOfWorkFactory,
    *,
    now: datetime | None = None,
    limit: int = DIGEST_CLAIM_LIMIT,
) -> int:
    """Send every digest due at `now`, up to `limit`; how many runs were sent.

    One transaction per schedule, so one schedule's failure leaves the others'
    digests sent and its own still due.
    """
    moment = now or datetime.now(timezone.utc)
    sent = 0
    for _ in range(limit):
        async with uow_factory() as uow:
            due = await claim_due_digest(uow.session, now=moment)
            if due is None:
                break
            sent += await _dispatch(uow, due, now=moment)
    return sent


async def _dispatch(uow: SqlAlchemyUnitOfWork, due: DueDigest, *, now: datetime) -> int:
    repository = HeldRunRepository(uow)
    # One past the bound, to know whether the next digest has anything to send.
    held = await repository.take_for_digest(
        due.schedule.id, limit=DIGEST_MAX_EVENTS + 1
    )
    if not held:
        return 0
    plan = plan_digest(held)
    sent = 0
    for batch in plan.batches:
        if await _send(
            uow, repository, due, batch, more_waiting=plan.more_waiting, now=now
        ):
            sent += 1
    if plan.more_waiting:
        # Not left for the next occurrence, which may be a day away and never
        # catch up -- or never come, when the digest was removed and claiming
        # this one cleared the cursor.
        await repository.continue_digest(
            due.schedule.id, at=now + DIGEST_CONTINUE_AFTER
        )
    return sent


async def _send(
    uow: SqlAlchemyUnitOfWork,
    repository: HeldRunRepository,
    due: DueDigest,
    batch: DigestBatch,
    *,
    more_waiting: bool,
    now: datetime,
) -> bool:
    schedule = due.schedule
    payload: dict[str, JsonValue] = {"events": batch.events, "held": len(batch.runs)}
    metadata: dict[str, JsonValue] = {
        "digest": True,
        "held": len(batch.runs),
        "more_waiting": more_waiting,
    }
    source_event_id = digest_event_id(due.due_at, batch.user_id)
    digest_run_id = await repository.insert_digest_run(
        schedule_id=schedule.id,
        user_id=batch.user_id,
        source_event_id=source_event_id,
        target_kind=schedule.target_kind,
        payload=payload,
        metadata=metadata,
        occurred_at=due.due_at,
    )
    if digest_run_id is None:
        # This occurrence went out already; its events stay held for the next.
        return False
    await repository.mark_digested(
        [run.id for run in batch.runs], digest_run_id=digest_run_id, now=now
    )
    uow.collect_events(
        [
            _fired(
                schedule,
                user_id=batch.user_id,
                payload=payload,
                metadata=metadata,
                due_at=due.due_at,
                source_event_id=source_event_id,
                digest_run_id=digest_run_id,
            )
        ]
    )
    return True


def plan_digest(held: Sequence[ScheduleRunEntity]) -> DigestPlan:
    """The held runs one digest sends, grouped by owner, inside its bounds.

    Oldest first, and stopping at the first that does not fit rather than
    skipping it, so events go out in the order they arrived. The first event
    always goes, as a stub if it must, or one oversized event would hold up
    every digest after it.
    """
    batches: dict[UUID, DigestBatch] = {}
    total = 0
    taken = 0
    for run in held[:DIGEST_MAX_EVENTS]:
        owner = run.user_id
        if owner is None:  # never taken: see `take_for_digest`
            continue
        event = digest_event(run)
        size = len(json.dumps(event, default=str))
        if taken and total + size > DIGEST_MAX_CHARS:
            break
        batch = batches.setdefault(owner, DigestBatch(user_id=owner))
        batch.runs.append(run)
        batch.events.append(event)
        total += size
        taken += 1
    return DigestPlan(batches=list(batches.values()), more_waiting=taken < len(held))


def digest_event(run: ScheduleRunEntity) -> JsonValue:
    """A held run's payload as a digest carries it: whole, or a stub when too large."""
    payload: dict[str, JsonValue] = dict(run.payload)
    characters = len(json.dumps(payload, default=str))
    if characters <= DIGEST_EVENT_MAX_CHARS:
        return payload
    return {
        "truncated": True,
        "characters": characters,
        "run_id": str(run.id),
        "source_event_id": run.source_event_id,
    }


def digest_event_id(due_at: datetime, user_id: UUID) -> str:
    """The digest run's `source_event_id`: the occurrence it went out at, and whose."""
    return (
        f"{DIGEST_EVENT_PREFIX}{due_at.astimezone(timezone.utc).isoformat()}:{user_id}"
    )


def _fired(
    schedule: ScheduleEntity,
    *,
    user_id: UUID,
    payload: dict[str, JsonValue],
    metadata: dict[str, JsonValue],
    due_at: datetime,
    source_event_id: str,
    digest_run_id: UUID,
) -> ScheduleFired:
    return ScheduleFired(
        schedule_id=schedule.id,
        user_id=user_id,
        schedule_type=schedule.schedule_type,
        pod_id=schedule.pod_id,
        account_id=schedule.account_id,
        payload=payload,
        metadata=metadata,
        llm_output={},
        scheduled_at=due_at,
        source_event_id=source_event_id,
        causation_id=digest_run_id,
    )
