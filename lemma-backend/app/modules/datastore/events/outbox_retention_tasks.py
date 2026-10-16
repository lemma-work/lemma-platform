"""Retention for the datastore database's own event outbox.

When pod schemas live in a database of their own, every record write stages
its event in *that* database's ``domain_event_outbox`` (see
``transactional_events.ensure_datastore_event_outbox``), and the datastore
outbox dispatcher publishes it from there. The hourly
``prune_event_delivery_records`` cron only ever opened the main database, so
this second outbox was never pruned: on a development install it reached 5.7M
published rows and 13 GB, filled the disk, and slowed the dispatcher's claim
query past its statement timeout.

Same rules and the same settings as the main outbox -- published rows past
``EVENT_COMPLETED_RETENTION_DAYS``, dead letters past
``EVENT_DEAD_LETTER_RETENTION_DAYS`` -- and nothing else: this database has no
inbox. Offset from the main sweep's minute so the two never contend for the
same worker slot.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.infrastructure.events.retention import prune_event_delivery_records
from app.core.infrastructure.jobs.streaq_runtime import Lane, streaq_cron
from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings

logger = get_logger(__name__)


def datastore_outbox_is_separate(
    database_url: str | None = None, datastore_database_url: str | None = None
) -> bool:
    """True when record events are staged outside the main database.

    When the two URLs match there is one outbox table, and the main sweep
    already covers it; pruning it twice would only race the same rows.
    """
    main_url = database_url if database_url is not None else settings.database_url
    datastore_url = (
        datastore_database_url
        if datastore_database_url is not None
        else datastore_settings.datastore_database_url
    ) or main_url
    return datastore_url != main_url


def _datastore_session_maker() -> Callable[[], AsyncSession]:
    from app.modules.datastore.infrastructure.session import (
        get_datastore_session_maker,
    )

    return get_datastore_session_maker()


async def prune_datastore_outbox(
    *,
    is_separate: Callable[[], bool] = datastore_outbox_is_separate,
    session_maker: Callable[[], Callable[[], AsyncSession]] = _datastore_session_maker,
    prune: Callable[..., Awaitable[dict[str, int]]] = prune_event_delivery_records,
) -> dict[str, int] | None:
    """One sweep; None when there is no separate datastore outbox to prune."""
    if not is_separate():
        return None
    return await prune(session_maker(), include_inbox=False)


async def _datastore_outbox_exists() -> bool:
    """Record writes stop staging outbox events once the change log is on, so
    a new install's datastore database never gets the table at all."""
    from sqlalchemy import text

    from app.modules.datastore.infrastructure.session import get_datastore_engine

    async with get_datastore_engine().connect() as conn:
        return bool(
            await conn.scalar(
                text("SELECT to_regclass('domain_event_outbox') IS NOT NULL")
            )
        )


@streaq_cron("37 * * * *", name="prune_datastore_outbox", lane=Lane.BULK)
async def prune_datastore_outbox_task() -> None:
    if datastore_outbox_is_separate() and not await _datastore_outbox_exists():
        return
    deleted = await prune_datastore_outbox()
    if deleted and (total := sum(deleted.values())):
        logger.debug(
            "datastore.outbox_retention.pruned.observed",
            deleted_count=total,
        )
