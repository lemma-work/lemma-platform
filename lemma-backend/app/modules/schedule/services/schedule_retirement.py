"""Standing schedules down when what they listen to stops existing.

Owned by this module for the same reason as its sibling in `connectors`: the
rows are this module's, and a webhook source that has learned its installation
is gone should be able to say so without knowing how `schedules` is shaped.
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.schedule.contracts.webhook_source import WebhookPayload
from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.infrastructure.models.schedule import Schedule


async def deactivate_matching_schedules(
    session: AsyncSession, *, criteria: WebhookPayload, reason: str
) -> int:
    """Deactivate the active webhook schedules whose config contains `criteria`.

    The reason is written into the config rather than dropped, so someone
    looking at a trigger that stopped can see *why* rather than finding it
    merely off. The routing key survives untouched, so repairing the cause and
    reactivating is enough -- nothing has to be rebuilt.
    """
    result = await session.execute(
        update(Schedule)
        .where(
            Schedule.schedule_type == ScheduleType.WEBHOOK.value,
            Schedule.is_active.is_(True),
            Schedule.config.op("@>")(criteria),
        )
        .values(
            is_active=False,
            config=Schedule.config.op("||")({"deactivated_reason": reason}),
        )
    )
    return int(result.rowcount or 0)
