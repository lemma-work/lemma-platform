"""The ``schedule_runs`` writes a fire makes on the work runtime.

A fire *reserves* its run: the row is written ``PROCESSING`` with no attempt
and no ``started_at``, in the transaction that decided to fire, beside the
work item that will start the target. Starting is a separate step that claims
the reservation exactly once (``begin_start``), so the unique
``(schedule_id, source_event_id)`` constraint is what makes a fire happen once,
and ``attempts = 0`` is what makes its target start once.

An LLM-filtered event is claimed ``FILTERING`` *before* the model is asked.
A replay of the filter finds the row and does not ask -- and pay -- again.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid7

from pydantic import JsonValue
from sqlalchemy import delete, func, select, type_coerce, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.schedule.domain.schedule import ScheduleRunEntity, ScheduleRunStatus
from app.modules.schedule.infrastructure.models.run import ScheduleRun

#: Statuses that are not a fire: the filter said no (or is still asking), or
#: the fire was turned away before it claimed a run.
_NOT_FIRES = (ScheduleRunStatus.FILTERED.value, ScheduleRunStatus.FILTERING.value)


@dataclass(frozen=True, slots=True)
class Reservation:
    """A run row the caller just wrote (``created``) or found already there."""

    run_id: UUID
    status: ScheduleRunStatus
    created: bool


class FireLedger:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve(
        self,
        *,
        schedule_id: UUID,
        user_id: UUID,
        source_event_id: str,
        target_kind: str,
        payload: Mapping[str, JsonValue],
        metadata: Mapping[str, JsonValue] | None,
        llm_output: Mapping[str, JsonValue] | None,
        source_occurred_at: datetime | None,
        status: ScheduleRunStatus = ScheduleRunStatus.PROCESSING,
    ) -> Reservation | None:
        """Write the run for this fire, or report the one already written.

        ``None`` only if the row vanished between the conflict and the read
        (its schedule was deleted), which the caller treats as nothing to do.
        """
        created = await self._session.scalar(
            insert(ScheduleRun)
            .values(
                id=uuid7(),
                schedule_id=schedule_id,
                user_id=user_id,
                source_event_id=source_event_id,
                status=status.value,
                attempts=0,
                target_kind=target_kind,
                target_run_id=str(uuid7()),
                payload=dict(payload),
                fire_metadata=dict(metadata or {}),
                llm_output=dict(llm_output or {}),
                source_occurred_at=source_occurred_at,
            )
            .on_conflict_do_nothing(constraint="uq_schedule_run_source_event")
            .returning(ScheduleRun.id)
        )
        if created is not None:
            return Reservation(created, status, True)
        existing = await self._session.execute(
            select(ScheduleRun.id, ScheduleRun.status).where(
                ScheduleRun.schedule_id == schedule_id,
                ScheduleRun.source_event_id == source_event_id,
            )
        )
        row = existing.one_or_none()
        if row is None:
            return None
        return Reservation(row.id, ScheduleRunStatus(row.status), False)

    async def promote_filtering(
        self, run_id: UUID, *, llm_output: Mapping[str, JsonValue]
    ) -> bool:
        """FILTERING → PROCESSING (reserved) once the filter let it through."""
        changed = await self._session.scalar(
            update(ScheduleRun)
            .where(
                ScheduleRun.id == run_id,
                ScheduleRun.status == ScheduleRunStatus.FILTERING.value,
            )
            .values(
                status=ScheduleRunStatus.PROCESSING.value,
                llm_output=dict(llm_output),
            )
            .returning(ScheduleRun.id)
        )
        return changed is not None

    async def settle_skipped(
        self,
        run_id: UUID,
        *,
        llm_output: Mapping[str, JsonValue] | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> bool:
        """A reserved or filtering run that will not start → FILTERED (skipped).

        ``metadata`` is merged into the run's, so a skip says why it skipped.
        """
        values: dict[str, object] = {
            "status": ScheduleRunStatus.FILTERED.value,
            "completed_at": datetime.now(timezone.utc),
            "payload": {},
        }
        if llm_output is not None:
            values["llm_output"] = dict(llm_output)
        if metadata:
            values["fire_metadata"] = ScheduleRun.fire_metadata.op("||")(
                type_coerce(dict(metadata), JSONB)
            )
        changed = await self._session.scalar(
            update(ScheduleRun)
            .where(
                ScheduleRun.id == run_id,
                ScheduleRun.attempts == 0,
                ScheduleRun.status.in_(
                    (
                        ScheduleRunStatus.FILTERING.value,
                        ScheduleRunStatus.PROCESSING.value,
                    )
                ),
            )
            .values(**values)
            .returning(ScheduleRun.id)
        )
        return changed is not None

    async def release_filtering(self, run_id: UUID) -> None:
        """Drop a FILTERING claim whose model call provably never happened.

        Only for a provider that refused before answering -- unavailable or
        rate limited -- so the retry may ask again without paying twice.
        """
        await self._session.execute(
            delete(ScheduleRun).where(
                ScheduleRun.id == run_id,
                ScheduleRun.status == ScheduleRunStatus.FILTERING.value,
            )
        )

    async def lock(self, run_id: UUID) -> ScheduleRun | None:
        return await self._session.get(ScheduleRun, run_id, with_for_update=True)

    async def begin_start(self, run_id: UUID) -> ScheduleRunEntity | None:
        """Take the reservation for starting its target, once.

        ``None`` when there is nothing to start: the row is gone, already
        started (by this path or the legacy fire consumer), or settled.
        """
        model = await self.lock(run_id)
        if not is_unstarted(model):
            return None
        assert model is not None
        model.attempts = 1
        model.started_at = datetime.now(timezone.utc)
        await self._session.flush()
        return model.to_entity()

    async def by_target(
        self, target_kind: str, target_run_id: str
    ) -> ScheduleRun | None:
        return await self._session.scalar(
            select(ScheduleRun)
            .where(
                ScheduleRun.target_kind == target_kind,
                ScheduleRun.target_run_id == target_run_id,
            )
            .with_for_update()
        )

    async def fires_since(
        self, schedule_id: UUID, *, since: datetime, limit: int
    ) -> int:
        """Fires (claimed runs, not skips) since ``since``, counted up to ``limit``.

        Bounded so the cost is the ceiling, not the schedule's history; served
        by ``ix_schedule_runs_schedule_fires``.
        """
        fires = (
            select(ScheduleRun.id)
            .where(
                ScheduleRun.schedule_id == schedule_id,
                ScheduleRun.created_at >= since,
                ScheduleRun.status.not_in(_NOT_FIRES),
            )
            .limit(limit)
            .subquery()
        )
        return int(
            await self._session.scalar(select(func.count()).select_from(fires)) or 0
        )


def is_unstarted(model: ScheduleRun | None) -> bool:
    """Reserved and not yet handed to its target by anyone."""
    return (
        model is not None
        and model.status == ScheduleRunStatus.PROCESSING.value
        and model.target_outcome is None
        and model.attempts == 0
    )


__all__ = ["FireLedger", "Reservation", "is_unstarted"]
