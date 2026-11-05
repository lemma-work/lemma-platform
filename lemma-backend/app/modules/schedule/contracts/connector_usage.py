"""Which schedules listen through a set of pod connectors.

For ``connectors``' installation impact preview: removing an installation stops
every schedule bound to one of its pod connectors, and the person about to
remove it is shown how many, and where, before anything is gone. A schedule
names its connector by ``(pod, slug)`` and holds an MCP event subscription by
id in ``config.provider_trigger_id``; both come back, so the caller can tell
which of its subscriptions belong to which pod.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.schedule.infrastructure.models.schedule import Schedule


@dataclass(frozen=True, slots=True)
class ScheduleOnConnector:
    schedule_id: UUID
    pod_id: UUID
    pod_connector_slug: str
    #: The subscription it holds at the provider, when it holds one.
    provider_trigger_id: str | None
    is_active: bool


async def schedules_on_pod_connectors(
    session: AsyncSession,
    connectors: Sequence[tuple[UUID, str]],
    *,
    limit: int,
) -> list[ScheduleOnConnector]:
    """The schedules bound to any of these ``(pod_id, slug)`` connectors,
    active or paused, at most ``limit`` of them."""
    if not connectors:
        return []
    rows = await session.execute(
        select(
            Schedule.id,
            Schedule.pod_id,
            Schedule.pod_connector_slug,
            Schedule.config["provider_trigger_id"].astext,
            Schedule.is_active,
        )
        .where(
            tuple_(Schedule.pod_id, Schedule.pod_connector_slug).in_(list(connectors)),
            Schedule.is_internal.is_(False),
        )
        .order_by(Schedule.created_at, Schedule.id)
        .limit(limit)
    )
    return [
        ScheduleOnConnector(
            schedule_id=schedule_id,
            pod_id=pod_id,
            pod_connector_slug=slug,
            provider_trigger_id=trigger or None,
            is_active=bool(active),
        )
        for schedule_id, pod_id, slug, trigger, active in rows
    ]


__all__ = ["ScheduleOnConnector", "schedules_on_pod_connectors"]
