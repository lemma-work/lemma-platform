"""The one question the datastore consumer asks about almost every event.

Separated from ``ScheduleRepository`` because it is not repository work: it
answers "should any of that machinery run at all", on the hottest path the
schedule module has. Every row written anywhere on the platform reaches it,
while only a few dozen pods hold a DATASTORE schedule -- so this is the query
that decides, for ~97% of events, that nothing else needs to happen.

Kept as a function over a session rather than a method on the repository so the
caller need not build a repository (and a message bus it will never publish
through) to ask it, and so the repository does not grow another job.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.infrastructure.models.schedule import Schedule


async def pod_has_active_datastore_schedules(
    session: AsyncSession, pod_id: UUID
) -> bool:
    """Whether anything in this pod is watching its tables at all.

    Deliberately pod-scoped rather than table-and-operation scoped, which is
    what ``find_by_pod_table_event`` answers with a jsonb lateral. This exists
    to be skippable work, so it has to stay cheaper than the thing it guards:
    as an ``EXISTS`` it rides the existing ``ix_schedules_pod_id`` index and
    measured 0.06ms against production, four buffer hits. The precise match
    still runs afterwards for the pods that pass.
    """
    return bool(
        await session.scalar(
            select(
                select(1)
                .where(
                    Schedule.pod_id == pod_id,
                    Schedule.schedule_type == ScheduleType.DATASTORE,
                    Schedule.is_active.is_(True),
                )
                .exists()
            )
        )
    )
