"""The run ledger's side of a triage: events it holds, and the digests that send them.

A held event is a `schedule_runs` row like any other, keyed on the schedule and
its `source_event_id`, so a redelivered event finds the row it already has. It
is `HELD` with `held_for` saying what it waits for, and `target_outcome` is set
to `HELD` too: that keeps it out of the recovery sweep's partial index, because
an event waiting for a digest or a person is not a lost dispatch.

Every way out is a compare-and-set on `held_for`, so two workers -- or a second
answer -- can move a held event at most once.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid7

from pydantic import JsonValue
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleRunEntity,
    ScheduleRunStatus,
)
from app.modules.schedule.domain.triage import (
    DIGEST_EVENT_PREFIX,
    TriageRoute,
    next_digest_at,
)
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.infrastructure.models.schedule import Schedule

_HELD = ScheduleRunStatus.HELD.value
_DISPATCHED = ScheduleRunStatus.DISPATCHED.value
_FILTERED = ScheduleRunStatus.FILTERED.value
#: What an act run is while it counts toward `act_per_hour`: claimed by its
#: target's module, or already handed to it.
_ACTING = (ScheduleRunStatus.PROCESSING.value, ScheduleRunStatus.DISPATCHED.value)


@dataclass(frozen=True, slots=True)
class DueDigest:
    """A schedule whose digest is due, claimed: its cursor has already moved on."""

    schedule: ScheduleEntity
    due_at: datetime


class HeldRunRepository:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.session = uow.session

    async def hold(
        self,
        *,
        schedule_id: UUID,
        user_id: UUID,
        source_event_id: str,
        target_kind: str,
        held_for: TriageRoute,
        payload: Mapping[str, JsonValue],
        metadata: Mapping[str, JsonValue],
        llm_output: Mapping[str, JsonValue],
    ) -> tuple[ScheduleRunEntity, bool] | None:
        """Hold an event once; the row as it stands, and whether this call wrote it.

        The row as it stands, not as this call would have written it: a
        redelivered event whose first delivery is already answered must not be
        asked about again, and its caller can only tell from the row.
        """
        created_id = await self.session.scalar(
            insert(ScheduleRun)
            .values(
                schedule_id=schedule_id,
                user_id=user_id,
                source_event_id=source_event_id,
                status=_HELD,
                target_outcome=_HELD,
                held_for=held_for.value,
                attempts=0,
                target_kind=target_kind,
                payload=dict(payload),
                fire_metadata=dict(metadata),
                llm_output=dict(llm_output),
                started_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(constraint="uq_schedule_run_source_event")
            .returning(ScheduleRun.id)
        )
        model = await self.session.scalar(
            select(ScheduleRun).where(
                ScheduleRun.schedule_id == schedule_id,
                ScheduleRun.source_event_id == source_event_id,
            )
        )
        if model is None:
            return None
        return model.to_entity(), created_id is not None

    async def get(self, run_id: UUID) -> ScheduleRunEntity | None:
        model = await self.session.get(ScheduleRun, run_id)
        return None if model is None else model.to_entity()

    async def settle_ask(
        self,
        run_id: UUID,
        *,
        route: TriageRoute,
        llm_output: Mapping[str, JsonValue],
        now: datetime,
    ) -> ScheduleRunEntity | None:
        """Send an event held for an answer where the answer says, at most once.

        `act` re-arms it as `RECEIVED` with a target run id, the shape a redrive
        has: the `schedule.fired` the caller stages with it is claimed by the
        target's module like any other. `digest` leaves it held, now for the
        digest. `ignore` ends it as skipped.
        """
        row = await self.session.scalar(
            update(ScheduleRun)
            .where(
                ScheduleRun.id == run_id,
                ScheduleRun.held_for == TriageRoute.ASK.value,
            )
            .values(**_settled(route, llm_output, now))
            .returning(ScheduleRun)
        )
        return None if row is None else row.to_entity()

    async def take_for_digest(
        self, schedule_id: UUID, *, limit: int
    ) -> list[ScheduleRunEntity]:
        """The schedule's events held for its digest, oldest first, locked.

        `SKIP LOCKED`, so a replica that reaches the same schedule takes none of
        the rows another is already sending. An event whose owner's account is
        gone has nobody to run as, so no digest takes it: it stays visibly held.
        """
        rows = await self.session.scalars(
            select(ScheduleRun)
            .where(
                ScheduleRun.schedule_id == schedule_id,
                ScheduleRun.held_for == TriageRoute.DIGEST.value,
                ScheduleRun.user_id.is_not(None),
            )
            .order_by(ScheduleRun.created_at, ScheduleRun.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [row.to_entity() for row in rows.all()]

    async def insert_digest_run(
        self,
        *,
        schedule_id: UUID,
        user_id: UUID,
        source_event_id: str,
        target_kind: str,
        payload: Mapping[str, JsonValue],
        metadata: Mapping[str, JsonValue],
        occurred_at: datetime,
    ) -> UUID | None:
        """The digest's own run, `RECEIVED` for its target to claim; None if it exists."""
        return await self.session.scalar(
            insert(ScheduleRun)
            .values(
                schedule_id=schedule_id,
                user_id=user_id,
                source_event_id=source_event_id,
                status=ScheduleRunStatus.RECEIVED.value,
                attempts=0,
                target_kind=target_kind,
                target_run_id=str(uuid7()),
                payload=dict(payload),
                fire_metadata=dict(metadata),
                llm_output={},
                source_occurred_at=occurred_at,
            )
            .on_conflict_do_nothing(constraint="uq_schedule_run_source_event")
            .returning(ScheduleRun.id)
        )

    async def mark_digested(
        self, run_ids: Sequence[UUID], *, digest_run_id: UUID, now: datetime
    ) -> int:
        """End held events as sent in one digest run.

        `target_outcome` as well as `status`, so a sent event leaves the
        recovery index for good: its outcome is the digest run's to report.
        """
        result = await self.session.execute(
            update(ScheduleRun)
            .where(
                ScheduleRun.id.in_(list(run_ids)),
                ScheduleRun.held_for == TriageRoute.DIGEST.value,
            )
            .values(
                status=_DISPATCHED,
                target_outcome=_DISPATCHED,
                held_for=None,
                digest_run_id=digest_run_id,
                completed_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

    async def acted_since(self, schedule_id: UUID, *, since: datetime) -> int:
        """Act runs the schedule's target has been handed since `since`.

        Digests are left out, both the digest runs and the events they sent:
        `act_per_hour` bounds the runs that started straight away.
        """
        count = await self.session.scalar(
            select(func.count())
            .select_from(ScheduleRun)
            .where(
                ScheduleRun.schedule_id == schedule_id,
                ScheduleRun.created_at >= since,
                ScheduleRun.status.in_(_ACTING),
                ScheduleRun.digest_run_id.is_(None),
                ScheduleRun.source_event_id.not_like(f"{DIGEST_EVENT_PREFIX}%"),
            )
        )
        return int(count or 0)


async def claim_due_digest(session: AsyncSession, *, now: datetime) -> DueDigest | None:
    """Take the next schedule whose digest is due, and move its cursor past `now`.

    `FOR UPDATE SKIP LOCKED` with the cursor advanced in the same transaction,
    as `claim_due_schedules` claims a TIME occurrence: exactly one replica sends
    a given digest. A backlog of missed occurrences is one digest, not one per
    occurrence. A schedule whose digest was removed has a cursor and no cadence:
    it sends what it held once more and its cursor clears.
    """
    row = await session.scalar(
        select(Schedule)
        .where(
            Schedule.next_digest_at.is_not(None),
            Schedule.next_digest_at <= now,
            Schedule.is_active.is_(True),
        )
        .order_by(Schedule.next_digest_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if row is None or row.next_digest_at is None:
        return None
    due_at = row.next_digest_at
    schedule = row.to_entity()
    row.next_digest_at = next_digest_at(schedule.triage, after=now)
    await session.flush()
    return DueDigest(schedule=schedule, due_at=due_at)


def _settled(
    route: TriageRoute, llm_output: Mapping[str, JsonValue], now: datetime
) -> dict[str, object]:
    output = dict(llm_output)
    if route is TriageRoute.ACT:
        return {
            "status": ScheduleRunStatus.RECEIVED.value,
            "target_outcome": None,
            "held_for": None,
            "target_run_id": str(uuid7()),
            "started_at": None,
            "completed_at": None,
            "llm_output": output,
        }
    if route is TriageRoute.DIGEST:
        return {"held_for": TriageRoute.DIGEST.value, "llm_output": output}
    if route is TriageRoute.IGNORE:
        return {
            "status": _FILTERED,
            "target_outcome": _FILTERED,
            "held_for": None,
            "completed_at": now,
            "llm_output": output,
        }
    raise ValueError("an answer settles a held event; it cannot ask again")
