"""The periodic that reads every pod's measure into its week.

Every ``datastore_outcome_read_interval_minutes`` it pages through the pods
whose outcome has a measure and writes a ``datastore.outcome_week`` item for
each, one page to a transaction. It reads nothing itself, so one slow or broken
measure costs its own pod's item and nobody else's. A page is the pods after
the last id of the one before, so a pod made or let go mid-sweep is read once
or not until the next tick, never twice.

Worker only (``register_periodic_work``).
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import WorkWriter, periodic_job, pod_namespace
from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.events.outcome_week_work import (
    OUTCOME_WEEK,
    OutcomeWeekPayload,
    outcome_week_key,
)
from app.modules.pod.contracts.outcome import pods_with_measures

logger = get_logger(__name__)

OUTCOME_WEEKS_SWEEP = "datastore.outcome_weeks"

#: Pods written per transaction: a page of small inserts, never a long one.
_PAGE = 500


async def queue_outcome_weeks(
    uow_factory: SessionUnitOfWorkFactory | None = None, *, page: int = _PAGE
) -> int:
    """Write a reading for every pod with a measure; answers how many."""
    factory = uow_factory or SessionUnitOfWorkFactory(async_session_maker)
    queued = 0
    after: UUID | None = None
    while True:
        async with factory() as uow:
            pod_ids = await pods_with_measures(uow.session, after=after, limit=page)
            work = WorkWriter.of(uow)
            for pod_id in pod_ids:
                await work.add(
                    OUTCOME_WEEK,
                    pod_namespace(pod_id),
                    OutcomeWeekPayload(pod_id=pod_id),
                    dedup_key=outcome_week_key(pod_id),
                    concurrency_key=outcome_week_key(pod_id),
                )
            await uow.commit()
        queued += len(pod_ids)
        if len(pod_ids) < page:
            return queued
        after = pod_ids[-1]


@periodic_job(
    OUTCOME_WEEKS_SWEEP,
    every=timedelta(minutes=datastore_settings.datastore_outcome_read_interval_minutes),
)
async def sweep_outcome_weeks() -> None:
    queued = await queue_outcome_weeks()
    logger.info("datastore.outcome_weeks.queued", pod_count=queued)
