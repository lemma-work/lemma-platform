"""Telling an owner the platform paused their schedule, in the pausing transaction.

The notice is a ``delivery`` work item written with the pause, so the two
commit together: no pause goes unexplained, and no email goes out for a pause
that rolled back. ``ScheduleDeactivated`` is still collected for anything else
that reads the stream.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter
from app.modules.schedule.domain.events.schedule import ScheduleDeactivated
from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.work import registration as _registration  # noqa: F401  registers the kinds this writes
from app.modules.schedule.work.kinds import (
    SCHEDULE_PAUSED_NOTICE,
    PausedNotice,
    schedule_namespace,
)


async def stage_paused_notice(
    uow: SqlAlchemyUnitOfWork,
    *,
    schedule_id: UUID,
    user_id: UUID,
    pod_id: UUID | None,
    schedule_type: ScheduleType,
    reason: str,
    consecutive_failures: int = 0,
    work: WorkWriter | None = None,
) -> None:
    uow.collect_events(
        [
            ScheduleDeactivated(
                schedule_id=schedule_id,
                user_id=user_id,
                pod_id=pod_id,
                schedule_type=schedule_type,
                consecutive_failures=consecutive_failures,
                reason=reason,
            )
        ]
    )
    await (work or WorkWriter.of(uow)).add(
        SCHEDULE_PAUSED_NOTICE,
        schedule_namespace(pod_id),
        PausedNotice(
            schedule_id=schedule_id,
            user_id=user_id,
            reason=reason,
            consecutive_failures=consecutive_failures,
        ),
        dedup_key=f"schedule-paused:{schedule_id}:{reason}",
    )
