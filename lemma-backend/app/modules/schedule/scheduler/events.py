"""Event emission for scheduled jobs using FastStream."""

from __future__ import annotations

from typing import Any, Dict
from uuid import UUID
from datetime import datetime, timezone

import redis.asyncio as redis
from faststream.redis import RedisBroker

from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.domain.events.schedule import ScheduleEvents, ScheduleFired
from app.core.config import settings
from app.core.infrastructure.events.stream_subscriber import ensure_named_groups
from app.core.log.log import get_logger

logger = get_logger(__name__)


class SchedulerEventEmitter:
    """Emits events to FastStream when scheduled jobs fire."""

    def __init__(self):
        self.broker: RedisBroker | None = None
        self._redis: redis.Redis | None = None
        self._started = False

    async def start(self):
        """Start the broker connection."""
        if not self._started:
            self.broker = RedisBroker(settings.redis_url)
            await self.broker.start()
            self._redis = redis.from_url(settings.redis_url, decode_responses=False)
            self._started = True
            logger.info("Scheduler event emitter started")

    async def stop(self):
        """Stop the broker connection."""
        if self._started and self.broker:
            await self.broker.stop()
            if self._redis is not None:
                await self._redis.aclose()
                self._redis = None
            self._started = False
            logger.info("Scheduler event emitter stopped")

    async def emit_scheduled_job_event(
        self, schedule_id: UUID, payload: Dict[str, Any] | None = None
    ):
        """Emit an event when a scheduled job fires.

        Args:
            schedule_id: The schedule ID that was scheduled
            payload: Optional payload data
        """
        if not self._started or not self.broker:
            logger.error("Event emitter not started, cannot emit event")
            return

        try:
            # Need to fetch user_id from schedule - for now emit with placeholder
            # The consumer will look up the schedule to get full context
            event = ScheduleFired(
                schedule_id=schedule_id,
                user_id=UUID(
                    "00000000-0000-0000-0000-000000000000"
                ),  # Placeholder - consumer resolves
                schedule_type=ScheduleType.TIME,
                payload=payload or {},
                scheduled_at=datetime.now(timezone.utc),
            )

            # Ensure the consumer groups exist immediately before XADD so a
            # subscriber that lost its group still receives this event (a group
            # recreated later at "$" would skip it). This pod (scheduler) never
            # imports the consuming subscribers, so it ensures the explicitly
            # declared groups by name rather than via the subscriber registry.
            if self._redis is not None:
                try:
                    await ensure_named_groups(
                        self._redis,
                        ScheduleEvents.STREAM,
                        ScheduleEvents.CONSUMER_GROUPS,
                    )
                except Exception as ensure_exc:  # noqa: BLE001
                    logger.warning(
                        f"Failed ensuring schedule_events groups before publish: {ensure_exc}"
                    )

            await self.broker.publish(event, stream=ScheduleEvents.STREAM)

            logger.info(f"Emitted scheduled job event for schedule {schedule_id}")
        except Exception as e:
            logger.error(f"Failed to emit scheduled job event: {e}", exc_info=True)


# Global event emitter instance
_event_emitter: SchedulerEventEmitter | None = None


def get_event_emitter() -> SchedulerEventEmitter:
    """Get the global event emitter instance."""
    global _event_emitter
    if _event_emitter is None:
        _event_emitter = SchedulerEventEmitter()
    return _event_emitter
