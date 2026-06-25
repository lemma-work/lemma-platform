"""Adapter for publishing schedule fired events to pubsub."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.modules.schedule.domain.events.schedule import ScheduleEvents, ScheduleFired
from app.modules.schedule.domain.interfaces import ScheduleEventPublisher
from app.modules.schedule.domain.schedule import ScheduleEntity
from app.core.log.log import get_logger
from app.core.pubsub.publisher import PubSubPublisher

logger = get_logger(__name__)


class RedisScheduleEventPublisher(ScheduleEventPublisher):
    """Publish ScheduleFired events to Redis stream."""

    async def publish_schedule_fired(
        self,
        schedule: ScheduleEntity,
        payload: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        llm_output: Optional[Dict[str, Any]] = None,
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
        )

        async with PubSubPublisher() as publisher:
            # Pass the declared consumer groups explicitly: this publisher runs in
            # the worker (datastore-fired schedules) AND the API pod (webhook-fired
            # schedules), and the API pod never imports the consuming subscribers,
            # so the subscriber registry alone would miss the group there.
            await publisher.publish(
                ScheduleEvents.STREAM,
                event,
                ensure_groups=ScheduleEvents.CONSUMER_GROUPS,
            )
            logger.info(
                "Published schedule event for schedule %s to %s",
                schedule.id,
                ScheduleEvents.STREAM,
            )
