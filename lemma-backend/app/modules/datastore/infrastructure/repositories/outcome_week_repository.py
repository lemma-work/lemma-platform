"""The weeks a pod's measure was read in: one row per pod and week."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.datastore.infrastructure.models import PodOutcomeWeekModel

_ROW = PodOutcomeWeekModel


@dataclass(frozen=True, slots=True)
class OutcomeWeek:
    """One week's last reading."""

    starts_on: date
    value: float | None
    target: float | None
    problem: str | None
    measured_at: datetime


class OutcomeWeekRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self, *, pod_id: UUID, measure_digest: str, week: OutcomeWeek
    ) -> None:
        """Keep ``week`` as its week's reading, over any earlier one.

        Only over an earlier one: a reading that lands late -- a retried job,
        or two workers reading the same pod -- never undoes a newer number.
        """
        statement = insert(_ROW).values(_values(pod_id, measure_digest, week))
        statement = statement.on_conflict_do_update(
            index_elements=[_ROW.pod_id, _ROW.starts_on],
            set_={
                "measure_digest": statement.excluded.measure_digest,
                "value": statement.excluded.value,
                "target": statement.excluded.target,
                "problem": None,
                "measured_at": statement.excluded.measured_at,
            },
            where=_ROW.measured_at <= statement.excluded.measured_at,
        )
        await self._session.execute(statement)

    async def record_problem(
        self, *, pod_id: UUID, measure_digest: str, week: OutcomeWeek
    ) -> None:
        """Say why the measure could not be read, keeping the week's number.

        The number stays only if the same measure answered it: one read by a
        measure since replaced is not this measure's week. And as with
        ``record``, only over an earlier reading, so a failure that lands late
        never undoes a newer number.
        """
        statement = insert(_ROW).values(_values(pod_id, measure_digest, week))
        same_measure = _ROW.measure_digest == statement.excluded.measure_digest
        statement = statement.on_conflict_do_update(
            index_elements=[_ROW.pod_id, _ROW.starts_on],
            set_={
                "problem": statement.excluded.problem,
                "value": case((same_measure, _ROW.value), else_=None),
                "target": case(
                    (same_measure, _ROW.target), else_=statement.excluded.target
                ),
                "measured_at": case(
                    (same_measure, _ROW.measured_at),
                    else_=statement.excluded.measured_at,
                ),
                "measure_digest": statement.excluded.measure_digest,
            },
            where=_ROW.measured_at <= statement.excluded.measured_at,
        )
        await self._session.execute(statement)

    async def weeks(
        self, *, pod_id: UUID, measure_digest: str, since: date, limit: int
    ) -> dict[date, OutcomeWeek]:
        """Up to ``limit`` weeks from ``since`` on that this measure answered,
        by Monday."""
        rows = await self._session.scalars(
            select(_ROW)
            .where(
                _ROW.pod_id == pod_id,
                _ROW.measure_digest == measure_digest,
                _ROW.starts_on >= since,
            )
            .order_by(_ROW.starts_on)
            .limit(limit)
        )
        return {
            row.starts_on: OutcomeWeek(
                starts_on=row.starts_on,
                value=row.value,
                target=row.target,
                problem=row.problem,
                measured_at=row.measured_at,
            )
            for row in rows
        }


def _values(pod_id: UUID, measure_digest: str, week: OutcomeWeek) -> dict[str, object]:
    return {
        "pod_id": pod_id,
        "starts_on": week.starts_on,
        "measure_digest": measure_digest,
        "value": week.value,
        "target": week.target,
        "problem": week.problem,
        "measured_at": week.measured_at,
    }


__all__ = ["OutcomeWeek", "OutcomeWeekRepository"]
