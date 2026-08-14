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
from app.core.log.log import get_logger
from app.core.request_context import bind_job_context, event_lineage

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)
schedule_counter = meter.create_counter("lemma.scheduler.jobs")
schedule_duration = meter.create_histogram("lemma.scheduler.job.duration", unit="ms")


class SchedulerEventEmitter:
    """Builds and stages the event a fired schedule produces.

    There is no lifecycle here on purpose. This used to carry a ``_started``
    flag with ``start``/``stop`` methods documented as owning a broker
    connection; they owned nothing, and only the scheduler sidecar ever called
    them. When the sidecar went, the flag could only produce a false failure --
    and it immediately did, refusing every fire in the worker with "emitter is
    not started" while the claim that produced it had already committed. A gate
    that verifies nothing and can only fail wrongly is worse than no gate.
    """

    async def stage_scheduled_job_event(
        self,
        uow,
        schedule_id: UUID,
        user_id: UUID | None = None,
        payload: Dict[str, Any] | None = None,
        *,
        scheduled_at: datetime,
    ):
        """Stage a fire onto the caller's transaction.

        The caller passes the unit of work that claimed the schedule, so the
        claim and the event it produces commit together. That is what makes a
        fire exactly-once: publishing separately left a window where the cursor
        had moved but the event did not exist, and the occurrence was gone --
        no retry, because the row no longer looked due.

        Staging is an INSERT into the outbox, not a Redis round trip, so keeping
        it inside the transaction costs one statement rather than a fan-out. The
        outbox dispatcher does the actual delivery, with the lease, retry and
        dead-letter behaviour it already has.

        Args:
            uow: the unit of work that claimed this occurrence
            schedule_id: The schedule ID that was scheduled
            user_id: Owner of the resulting run; absent on timers, which resolve
                their owner from the run row
            payload: Optional payload data
        """
        scheduled_at = scheduled_at.astimezone(timezone.utc)
        source_event_id = f"cron:{schedule_id}:{scheduled_at.isoformat()}"
        event = ScheduleFired(
            schedule_id=schedule_id,
            user_id=user_id,
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
                    # Onto the caller's transaction rather than a fresh one of
                    # our own. `EventPublisher.publish` opens its own UoW, which
                    # is right for a caller that has none -- but here it would
                    # split the claim and its event across two commits.
                    uow.collect_events([event])
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
