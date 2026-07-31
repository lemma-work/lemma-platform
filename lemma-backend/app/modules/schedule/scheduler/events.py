"""Durable event emission for scheduled jobs."""

from __future__ import annotations

import asyncio
from typing import Any, Dict
from uuid import UUID
from datetime import datetime, timezone
import time

from opentelemetry import metrics, trace
from opentelemetry.trace import SpanKind

from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.domain.events.schedule import ScheduleFired
from app.core.infrastructure.events.publisher import EventPublisher
from app.core.log.log import get_logger
from app.core.request_context import bind_job_context, event_lineage

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)
schedule_counter = meter.create_counter("lemma.scheduler.jobs")
schedule_duration = meter.create_histogram("lemma.scheduler.job.duration", unit="ms")


class SchedulerEventEmitter:
    """Emits events to FastStream when scheduled jobs fire."""

    def __init__(self):
        self._started = False

    async def start(self):
        """Start the broker connection."""
        if not self._started:
            self._started = True

    async def stop(self):
        """Stop the broker connection."""
        if self._started:
            self._started = False

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
        started_at = time.perf_counter()
        outcome = "succeeded"
        try:
            with tracer.start_as_current_span(
                "lemma.scheduler.job",
                kind=SpanKind.PRODUCER,
                attributes={
                    "lemma.event_id": str(event.event_id),
                    "lemma.event_type": event.event_type,
                    "lemma.task_name": "schedule.fire",
                },
            ) as span:
                with (
                    bind_job_context(
                        job_id=str(schedule_id),
                        task_name="schedule.fire",
                    ),
                    event_lineage(
                        correlation_id=event.correlation_id or event.event_id,
                        event_id=event.event_id,
                        causation_id=event.causation_id,
                        request_id=event.request_id,
                        event_type=event.event_type,
                        consumer="scheduler.emitter",
                    ),
                ):
                    await EventPublisher.publish(event.stream_name(), event)
                    span.set_attribute("lemma.outcome", outcome)
                    logger.debug(
                        "schedule.event.staged",
                        schedule_id=str(schedule_id),
                        source_event_id=source_event_id,
                    )
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception:
            outcome = "failed"
            raise
        finally:
            labels = {"task_name": "schedule.fire", "outcome": outcome}
            schedule_counter.add(1, labels)
            schedule_duration.record(
                (time.perf_counter() - started_at) * 1000,
                labels,
            )


# Global event emitter instance
_event_emitter: SchedulerEventEmitter | None = None


def get_event_emitter() -> SchedulerEventEmitter:
    """Get the global event emitter instance."""
    global _event_emitter
    if _event_emitter is None:
        _event_emitter = SchedulerEventEmitter()
    return _event_emitter
