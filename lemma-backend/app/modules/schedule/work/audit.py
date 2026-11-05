"""``schedule.arm_audit``: every active TIME schedule has a fire item.

Each write path that makes a TIME schedule due writes its ``schedule.fire``
item in the same transaction, and each fire writes the next, so the invariant
holds by construction. This checks it anyway, every ten minutes: one anti-join
of active TIME schedules against queued or running fire items, and a
``schedule.arm`` for each one missing -- which computes the cursor and writes
the item, as for a row written before the work runtime. It re-arms; it never
fires, so a schedule it finds runs its next occurrence, not a missed one.

``lemma.schedule.rearmed`` counts what it found. Anything above zero is a
path that lost a fire item, which is the thing to go and find.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from opentelemetry import metrics
from sqlalchemy import text

from app.core.infrastructure.work import JobContext
from app.core.log.log import get_logger
from app.modules.schedule.work.kinds import (
    SCHEDULE_ARM,
    SCHEDULE_FIRE,
    ArmAudit,
    ScheduleRef,
    schedule_namespace,
)

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)
rearmed = meter.create_counter(
    "lemma.schedule.rearmed",
    description=(
        "Active TIME schedules the arm audit found without a fire item and "
        "re-armed. Expected to stay at zero."
    ),
)

ARM_AUDIT_EVERY = timedelta(minutes=10)
#: Schedules re-armed per audit; more waits for the next one.
ARM_AUDIT_LIMIT = 500

_UNARMED = text(
    """
    SELECT s.id, s.pod_id FROM schedules s
     WHERE s.schedule_type = 'TIME' AND s.is_active
       AND NOT EXISTS (
           SELECT 1 FROM work_items w
            WHERE w.kind = :fire AND w.state IN ('queued', 'running')
              AND w.payload->>'schedule_id' = s.id::text)
     ORDER BY s.id
     LIMIT :limit
    """
)


async def run_arm_audit(ctx: JobContext, payload: ArmAudit) -> None:
    del payload
    async with ctx.complete_work() as uow:
        rows = (
            await uow.session.execute(
                _UNARMED, {"fire": SCHEDULE_FIRE, "limit": ARM_AUDIT_LIMIT}
            )
        ).all()
        work = ctx.writer(uow)
        for schedule_id, pod_id in rows:
            await work.add(
                SCHEDULE_ARM,
                schedule_namespace(UUID(str(pod_id)) if pod_id else None),
                ScheduleRef(schedule_id=schedule_id),
                dedup_key=f"schedule-arm:{schedule_id}",
            )
    if rows:
        rearmed.add(len(rows))
        logger.warning(
            "schedule.arm_audit.rearmed",
            rearmed=len(rows),
            schedule_ids=[str(row[0]) for row in rows[:20]],
        )


__all__ = ["ARM_AUDIT_EVERY", "ARM_AUDIT_LIMIT", "run_arm_audit"]
