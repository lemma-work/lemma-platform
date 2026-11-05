"""``lemma.schedules.active``: how many schedules are live, by type and target.

One grouped count over active, non-internal schedules, on the work-gauge
loop. ``ix_schedules_type_active`` is on ``(schedule_type, is_active)``. Every
replica samples the same number, so a dashboard takes the max, not the sum.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.work.gauges import register_gauge_sampler
from app.core.observability import product_metrics

ACTIVE_BY_TYPE_AND_TARGET = text(
    """
    SELECT schedule_type,
           workflow_id IS NOT NULL AS has_workflow,
           agent_id IS NOT NULL AS has_agent,
           count(*) AS n
      FROM schedules
     WHERE is_active AND NOT is_internal
     GROUP BY 1, 2, 3
    """
)


async def sample_active_schedules(session: AsyncSession) -> None:
    counts: dict[tuple[str, str], int] = {}
    for row in (await session.execute(ACTIVE_BY_TYPE_AND_TARGET)).all():
        key = (
            product_metrics.schedule_type_label(row.schedule_type),
            product_metrics.schedule_target_label(
                agent_id=True if row.has_agent else None,
                workflow_id=True if row.has_workflow else None,
            ),
        )
        counts[key] = counts.get(key, 0) + int(row.n)
    product_metrics.snapshot.schedules_active = counts


def _forget() -> None:
    product_metrics.snapshot.schedules_active = {}


register_gauge_sampler("schedules_active", sample_active_schedules, forget=_forget)
