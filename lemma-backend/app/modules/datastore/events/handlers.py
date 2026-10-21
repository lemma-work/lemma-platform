"""The datastore's signed-link retention, as periodic work.

File processing, its dispatcher and recovery crons, and path cleanup moved to
``file_work`` (``file.process`` / ``file.cleanup`` work items).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.modules.datastore.config import datastore_settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
)
from app.modules.datastore.infrastructure.repositories.signed_link_repository import (
    SignedLinkRepository,
)
from app.core.infrastructure.work import periodic_job
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Most a single nightly sweep will delete. A backlog drains over successive
# nights rather than holding the table for one very long run.
_SIGNED_LINK_PURGE_CEILING = 50_000


@periodic_job("datastore.signed_links_purge", every=timedelta(days=1))
async def purge_expired_signed_links() -> None:
    """Delete short-link rows whose links expired.

    Redis drops its own copy on TTL, so an expired link stops resolving whether
    or not this runs; the row is what would otherwise accumulate forever. Daily
    and off-peak because nothing depends on the timing — a stale row costs a few
    bytes and answers nothing, since the serving path checks expiry itself.

    Deletes in bounded batches, and keeps rows for a grace period past expiry so
    a pod listing with ``include_dead`` can still show what was recently handed
    out.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=datastore_settings.datastore_signed_url_row_retention_seconds
    )
    total = 0
    while True:
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            deleted = await SignedLinkRepository(uow).delete_expired(before=cutoff)
            await uow.commit()
        total += deleted
        if deleted == 0 or total >= _SIGNED_LINK_PURGE_CEILING:
            break
    if total:
        logger.info(
            "datastore.signed_url.purged_expired_link_rows.observed",
            count=total,
        )
