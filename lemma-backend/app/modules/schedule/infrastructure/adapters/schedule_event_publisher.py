"""Durable adapter for publishing schedule-fired domain events."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.infrastructure.events.publisher import EventPublisher
from app.modules.schedule.domain.events.schedule import ScheduleFired
from app.modules.schedule.domain.interfaces import ScheduleEventPublisher
from app.modules.schedule.domain.schedule import ScheduleEntity
from app.core.log.log import get_logger

logger = get_logger(__name__)


class DurableScheduleEventPublisher(ScheduleEventPublisher):
    """Stage ScheduleFired events in PostgreSQL for outbox delivery."""

    async def publish_schedule_fired(
        self,
        schedule: ScheduleEntity,
        payload: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        llm_output: Optional[Dict[str, Any]] = None,
        source_event_id: str | None = None,
    ) -> None:
        event = ScheduleFired(
            schedule_id=schedule.id,
            user_id=schedule.user_id,
            schedule_type=schedule.schedule_type,
            payload=payload,
            metadata=metadata,
            account_id=schedule.account_id,
            pod_id=schedule.pod_id,
            llm_output=llm_output,
            source_event_id=source_event_id,
        )
        await EventPublisher.publish(event.stream_name(), event)
        logger.debug(
            "schedule.schedule_event_publisher.staged_schedule_event_schedule_s.observed",
            source_event_id=source_event_id,
        )
