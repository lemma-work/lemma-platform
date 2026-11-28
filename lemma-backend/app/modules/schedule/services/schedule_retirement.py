"""Standing schedules down when what they listen to stops existing.

Owned by this module for the same reason as its sibling in `connectors`: the
rows are this module's, and a webhook source that has learned its installation
is gone should be able to say so without knowing how `schedules` is shaped.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.schedule.contracts.webhook_source import WebhookPayload
from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.infrastructure.models.schedule import Schedule


async def deactivate_matching_schedules(
    session: AsyncSession,
    *,
    criteria: WebhookPayload,
    reason: str,
    organization_id: UUID | None = None,
) -> int:
    """Deactivate the active webhook schedules whose config contains `criteria`.

    The reason is written into the config rather than dropped, so someone
    looking at a trigger that stopped can see *why* rather than finding it
    merely off. The routing key survives untouched, so repairing the cause and
    reactivating is enough -- nothing has to be rebuilt.

    ``organization_id`` stands down only the schedules in that organization's
    pods: one organization letting go of something others still hold, whose
    schedules match the same criteria and keep firing.
    """
    scope = [
        Schedule.schedule_type == ScheduleType.WEBHOOK.value,
        Schedule.is_active.is_(True),
        Schedule.config.op("@>")(criteria),
    ]
    if organization_id is not None:
        from app.modules.pod.contracts.agent_access import pod_organization_ids

        matched = (
            await session.execute(select(Schedule.id, Schedule.pod_id).where(*scope))
        ).all()
        owners = await pod_organization_ids(
            SqlAlchemyUnitOfWork(session),
            {pod_id for _, pod_id in matched if pod_id is not None},
        )
        ours = [
            schedule_id
            for schedule_id, pod_id in matched
            if pod_id is not None and owners.get(pod_id) == organization_id
        ]
        if not ours:
            return 0
        scope.append(Schedule.id.in_(ours))
    result = await session.execute(
        update(Schedule)
        .where(*scope)
        .values(
            is_active=False,
            config=Schedule.config.op("||")({"deactivated_reason": reason}),
        )
    )
    return int(result.rowcount or 0)
