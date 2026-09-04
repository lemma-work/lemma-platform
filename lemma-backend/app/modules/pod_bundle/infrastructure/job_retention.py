"""Deleting pod-bundle job rows past retention.

Every export, import and publish writes one ``pod_bundle_jobs`` row plus one
``pod_bundle_job_steps`` row per plan step, and nothing ever removed them: the
store's ``delete_*`` methods drop only the Redis mirror, so a 200-step import
left 201 rows forever. ``ix_pod_bundle_jobs_completed_retention`` -- a partial
index on ``completed_at`` -- has been in the schema since the tables were added,
with no code that used it. This is the query it was built for.

Kept out of :mod:`state_store`, which is about one job's state; this is about
every job's remains.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select

from app.core.infrastructure.db.session import async_session_maker
from app.modules.pod_bundle.infrastructure.models import PodBundleJob

#: How long a finished job's row survives. The rows outlive the archives they
#: describe on purpose: a pod's bundle history is read from them long after the
#: staged zip is reclaimed. 30 days is well past the longest state TTL (an
#: export's, 24h) and past any support window that would want the step-by-step
#: record of an import.
JOB_ROW_RETENTION_SECONDS = 30 * 24 * 60 * 60

#: Deleted per sweep. The cron runs twice an hour, so a backlog drains at ~48k
#: rows/day without one long delete holding locks on a busy table.
JOB_ROW_PURGE_LIMIT = 1_000


async def purge_completed_jobs(*, cutoff: datetime, limit: int) -> int:
    """Delete finished job rows completed before ``cutoff``; returns how many.

    Steps go with their job through the FK's ``ON DELETE CASCADE``. Bounded per
    run and ordered oldest-first so a backlog drains over several sweeps instead
    of one long-running delete; a job still running has a null ``completed_at``
    and is never matched.
    """
    async with async_session_maker() as session, session.begin():
        doomed = (
            await session.scalars(
                select(PodBundleJob.id)
                .where(
                    PodBundleJob.completed_at.is_not(None),
                    PodBundleJob.completed_at < cutoff,
                )
                .order_by(PodBundleJob.completed_at)
                .limit(limit)
            )
        ).all()
        if not doomed:
            return 0
        await session.execute(delete(PodBundleJob).where(PodBundleJob.id.in_(doomed)))
        return len(doomed)
