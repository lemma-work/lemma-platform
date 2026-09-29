"""The nightly cron that drops deleted pods' datastore schemas."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_runtime import Lane, streaq_cron
from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure.pod_schema_catalog import PodSchemaCatalog
from app.modules.datastore.infrastructure.session import get_datastore_engine
from app.modules.datastore.services.orphan_schema_reaper import (
    OrphanSchemaReaper,
    ReapSummary,
)
from app.modules.pod.contracts.liveness import pods_gone_since

logger = get_logger(__name__)


async def _gone_pods(pod_ids: Collection[UUID], deleted_before: datetime) -> set[UUID]:
    return await pods_gone_since(
        SessionUnitOfWorkFactory(async_session_maker),
        pod_ids,
        deleted_before=deleted_before,
    )


async def reap_orphan_pod_schemas(now: datetime | None = None) -> ReapSummary | None:
    """One pass; None when retention is 0, which switches the cleanup off."""
    days = datastore_settings.datastore_orphan_schema_retention_days
    if days <= 0:
        return None
    reaper = OrphanSchemaReaper(
        PodSchemaCatalog(get_datastore_engine()),
        _gone_pods,
        retention=timedelta(days=days),
    )
    summary = await reaper.run(now or datetime.now(timezone.utc))
    if summary.dropped or summary.failed:
        logger.info(
            "datastore.orphan_schemas.reaped",
            scanned_count=summary.scanned,
            dropped_count=summary.dropped,
            failed_count=summary.failed,
            retention_days=days,
        )
    return summary


@streaq_cron("53 3 * * *", name="reap_orphan_pod_schemas", lane=Lane.BULK)
async def reap_orphan_pod_schemas_cron() -> None:
    await reap_orphan_pod_schemas()
