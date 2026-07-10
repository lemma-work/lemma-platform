"""Durable event emission for scheduled jobs."""

from __future__ import annotations

from typing import Any, Dict
from uuid import UUID
from datetime import datetime, timezone

from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.domain.events.schedule import ScheduleFired
from app.core.infrastructure.events.publisher import EventPublisher
from app.core.log.log import get_logger

logger = get_logger(__name__)


class SchedulerEventEmitter:
    """Emits events to FastStream when scheduled jobs fire."""

    def __init__(self):
        self._started = False

    async def start(self):
        """Start the broker connection."""
        if not self._started:
            self._started = True
            logger.info("Scheduler event emitter started")

    async def stop(self):
        """Stop the broker connection."""
        if self._started:
            self._started = False
            logger.info("Scheduler event emitter stopped")

    async def emit_scheduled_job_event(
        self,
        schedule_id: UUID,
        payload: Dict[str, Any] | None = None,
        *,
        scheduled_at: datetime,
    ):
        """Emit an event when a scheduled job fires.

        Args:
            schedule_id: The schedule ID that was scheduled
            payload: Optional payload data
        """
        if not self._started:
            raise RuntimeError("Scheduler event emitter is not started")

        scheduled_at = scheduled_at.astimezone(timezone.utc)
        source_event_id = f"cron:{schedule_id}:{scheduled_at.isoformat()}"
        event = ScheduleFired(
            schedule_id=schedule_id,
            user_id=UUID("00000000-0000-0000-0000-000000000000"),
            schedule_type=ScheduleType.TIME,
            payload=payload or {},
            scheduled_at=scheduled_at,
            source_event_id=source_event_id,
        )
        await EventPublisher.publish(event.stream_name(), event)
        logger.info(
            "Staged scheduled job event",
            schedule_id=str(schedule_id),
            source_event_id=source_event_id,
        )


# Global event emitter instance
_event_emitter: SchedulerEventEmitter | None = None


def get_event_emitter() -> SchedulerEventEmitter:
    """Get the global event emitter instance."""
    global _event_emitter
    if _event_emitter is None:
        _event_emitter = SchedulerEventEmitter()
    return _event_emitter
