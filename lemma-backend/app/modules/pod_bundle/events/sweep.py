"""The pod-bundle sweep cron.

Reclaims staged archives whose job state has expired, terminalizes jobs a
crashed worker left non-terminal, and deletes finished job rows past retention.

Its own module rather than another 100 lines of ``handlers``: the sweep shares
nothing with the export/import/publish tasks except the state store, runs on a
schedule instead of in response to a request, and ``handlers`` is well past the
file-size ceiling.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.infrastructure.jobs.streaq_runtime import Lane, streaq_cron
from app.core.log.log import get_logger
from app.modules.pod_bundle.domain.state import (
    ExportStatus,
    ImportStatus,
    PublishStatus,
)
from app.modules.pod_bundle.infrastructure import publish_lock
from app.modules.pod_bundle.infrastructure.job_retention import (
    JOB_ROW_PURGE_LIMIT,
    JOB_ROW_RETENTION_SECONDS,
    purge_completed_jobs,
)
from app.modules.pod_bundle.infrastructure.realtime import (
    error_payload,
    publish_bundle_event,
)
from app.modules.pod_bundle.infrastructure.staging import BundleStagingStorage
from app.modules.pod_bundle.infrastructure.state_store import (
    get_pod_bundle_state_store,
)

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# A non-terminal job untouched for longer than this is presumed dead (worker
# crash/restart): the apply job's own timeout is 1800s, so ~40min leaves a wide
# margin before the sweep intervenes.
_STUCK_AFTER_SECONDS = 40 * 60


@streaq_cron("13-59/30 * * * *", name="sweep_pod_bundle_staging", lane=Lane.BULK)
async def sweep_pod_bundle_staging() -> None:
    """Reclaim staged archives whose ephemeral state has expired, mark crashed
    jobs FAILED so the UI stops showing them as in-progress, and delete job rows
    past retention. Durable recovery scans PostgreSQL first, independently of
    object-store inventory."""
    reclaimed, recovered, purged = await _sweep(
        get_pod_bundle_state_store(), BundleStagingStorage()
    )
    if reclaimed or recovered or purged:
        logger.debug(
            "pod_bundle.sweep.swept",
            reclaimed=reclaimed,
            recovered=recovered,
            purged=purged,
        )


async def _sweep(store, staging) -> tuple[int, int, int]:
    # Per-kind archive retention is driven by the state TTL, not this cron: a
    # READY export is written with the export TTL (default 24h) while imports
    # use the default ~6h, so an export's state (and thus its archive, reclaimed
    # only once the state is gone) naturally outlives an import's. Job *rows*
    # are the exception, and are the one thing this cron dates itself.
    cutoff = _now() - timedelta(seconds=_STUCK_AFTER_SECONDS)

    reclaimed = 0
    recovered_states = await store.recover_stale_jobs(cutoff=cutoff)
    await publish_lock.release_recovered_publish_locks(recovered_states)
    recovered = len(recovered_states)
    for state in recovered_states:
        job_id = getattr(
            state,
            "import_id",
            getattr(state, "export_id", getattr(state, "publish_id", None)),
        )
        if job_id is not None:
            await publish_bundle_event(
                job_id,
                error_payload(state.error or "Job interrupted.", state.seq),
            )
    for kind, get_state, save_state, failed_status in (
        ("pod-imports", store.get_import, store.save_import, ImportStatus.FAILED),
        ("pod-exports", store.get_export, store.save_export, ExportStatus.FAILED),
        (
            "pod-publishes",
            store.get_publish,
            store.save_publish,
            PublishStatus.FAILED,
        ),
    ):
        try:
            archives = await staging.list_archives(kind)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - one kind's outage is not the sweep's
            # Degraded, and previously silent: this whole kind's orphaned bytes
            # go unreclaimed for the run, and nothing said so at INFO.
            logger.warning(
                "pod_bundle.sweep.archive_listing_unavailable.degraded",
                job_kind=kind,
                exc_info=True,
            )
            continue
        for job_id, _ in archives:
            state = await get_state(job_id)
            if state is None:
                # State TTL expired → the job is unreferenceable; reclaim bytes.
                try:
                    await staging.delete_archive(kind, job_id)  # type: ignore[arg-type]
                    reclaimed += 1
                except Exception:  # noqa: BLE001 - one archive is not the sweep
                    # The bytes stay until a later run reclaims them, so this is
                    # degraded rather than fatal — but an object store that
                    # refuses every delete is only visible if it is said.
                    logger.warning(
                        "pod_bundle.sweep.archive_delete_failed.degraded",
                        job_kind=kind,
                        job_id=job_id,
                        exc_info=True,
                    )
                continue
            if not state.is_terminal and state.updated_at < cutoff:
                state.status = failed_status
                state.error = "Interrupted (worker restart or crash); start over."
                state.completed_at = _now()
                await save_state(state)  # type: ignore[arg-type]
                await publish_bundle_event(
                    job_id, error_payload(state.error, state.seq)
                )
                recovered += 1

    # Last, so a failure here cannot cost the reclaimed bytes and un-stuck jobs
    # above -- those are what an operator is waiting on, and are already written.
    purged = await purge_completed_jobs(
        cutoff=_now() - timedelta(seconds=JOB_ROW_RETENTION_SECONDS),
        limit=JOB_ROW_PURGE_LIMIT,
    )

    return reclaimed, recovered, purged
