"""How much work is waiting, as opposed to how fast it is being done.

Throughput and duration cannot distinguish a worker that has nothing to do from
one that has stopped doing it, and a healthy publish rate can sit in front of a
growing pile of unpublished events. Both look identical on a rate graph. These
gauges are the missing half.

Sampling is a background loop rather than an observable-gauge callback because
every reading here is I/O -- Redis for the queues, PostgreSQL for the event
tables -- and OpenTelemetry's observable callbacks are synchronous. The loop
writes into a module-level snapshot and the callbacks read it, which is the
same shape ``loop_watchdog`` uses for its lag gauge.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation
from sqlalchemy import func, select

from app.core.infrastructure.events.models import DomainEventInbox, DomainEventOutbox
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)


@dataclass
class _Backlog:
    """Last successful reading of each backlog, held for the gauge callbacks.

    An object rather than bare module globals, matching ``_LagGauge`` in
    ``loop_watchdog``: the sampler writes and never reads, so plain globals
    would need a ``global`` declaration that reads as a dead store to anyone
    (and to any analyser) looking at that function alone.

    ``None`` and an empty mapping mean "not measured", which is deliberately
    distinct from a measured zero. A gauge reports a level, so a reading left
    in place after sampling starts failing flatlines at whatever was last true
    -- and a flat line reads as a healthy steady state rather than an outage.
    """

    queue_depth: dict[str, int] = field(default_factory=dict)
    outbox_pending: int | None = None
    inbox_pending: int | None = None

    def forget_queues(self) -> None:
        self.queue_depth = {}

    def forget_event_tables(self) -> None:
        self.outbox_pending = None
        self.inbox_pending = None


_backlog = _Backlog()

_queue_incident = DependencyIncident("backlog.queue_depth", logger=logger)
_events_incident = DependencyIncident("backlog.event_tables", logger=logger)


def _observe_queue_depth(options: CallbackOptions) -> Iterable[Observation]:
    del options
    return [
        Observation(depth, {"lane": lane})
        for lane, depth in _backlog.queue_depth.items()
    ]


def _observe_outbox_pending(options: CallbackOptions) -> Iterable[Observation]:
    del options
    pending = _backlog.outbox_pending
    return [] if pending is None else [Observation(pending)]


def _observe_inbox_pending(options: CallbackOptions) -> Iterable[Observation]:
    del options
    pending = _backlog.inbox_pending
    return [] if pending is None else [Observation(pending)]


meter.create_observable_gauge(
    "lemma.worker.queue.depth",
    callbacks=[_observe_queue_depth],
    description="Jobs waiting on a worker lane, including scheduled ones.",
)
meter.create_observable_gauge(
    "lemma.event.outbox.pending",
    callbacks=[_observe_outbox_pending],
    description="Outbox rows neither published nor dead-lettered.",
)
meter.create_observable_gauge(
    "lemma.event.inbox.pending",
    callbacks=[_observe_inbox_pending],
    description="Inbox rows still being processed.",
)


async def _sample_queue_depth() -> None:
    from app.core.infrastructure.jobs.streaq_runtime import LANE_WORKERS

    depth = {
        lane.value: await worker.queue_size() for lane, worker in LANE_WORKERS.items()
    }
    # Replaced wholesale rather than updated in place, so a lane that stops
    # reporting disappears instead of keeping its last value forever.
    _backlog.queue_depth = depth


async def _sample_event_tables(session_maker) -> None:
    async with session_maker() as session:
        # Both counts ride an existing partial/leading index over the small
        # unfinished set, so neither scans the table -- which matters, because
        # a gauge that measures a backlog must not become part of it.
        outbox_pending = await session.scalar(
            select(func.count())
            .select_from(DomainEventOutbox)
            .where(
                DomainEventOutbox.published_at.is_(None),
                DomainEventOutbox.dead_lettered_at.is_(None),
            )
        )
        inbox_pending = await session.scalar(
            select(func.count())
            .select_from(DomainEventInbox)
            .where(DomainEventInbox.status == "PROCESSING")
        )
    # Assigned only once both counts succeeded, so a failure part-way through
    # cannot leave one table's reading fresh and the other's stale.
    _backlog.outbox_pending = outbox_pending
    _backlog.inbox_pending = inbox_pending


async def backlog_gauge_loop(session_maker, *, interval_seconds: float) -> None:
    """Refresh every backlog reading at the configured cadence.

    Sampling faster than the metric export interval only burns queries -- the
    exporter reports whatever the last callback returned either way.
    """
    if interval_seconds <= 0:
        return
    while True:
        try:
            await _sample_queue_depth()
            _queue_incident.record_success()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _backlog.forget_queues()
            _queue_incident.record_failure(error_type=type(exc).__name__)

        try:
            await _sample_event_tables(session_maker)
            _events_incident.record_success()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _backlog.forget_event_tables()
            _events_incident.record_failure(error_type=type(exc).__name__)

        await asyncio.sleep(interval_seconds)
