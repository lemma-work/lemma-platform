"""The daily sweep of the outbound log; see ``outbound_message_repository``."""

from __future__ import annotations

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.jobs.lanes import Lane
from app.core.infrastructure.jobs.streaq_runtime import streaq_cron
from app.core.log.log import get_logger
from app.modules.agent_surfaces.infrastructure.repositories.outbound_message_repository import (  # noqa: E501
    prune_outbound_messages,
)

logger = get_logger(__name__)


# Daily, off the hour: a log nothing reads once its statuses have arrived.
@streaq_cron("41 4 * * *", name="prune_surface_outbound_messages", lane=Lane.BULK)
async def prune_surface_outbound_messages() -> None:
    """Forget sends past the window a status or a quote could still name them."""
    removed = await prune_outbound_messages(async_session_maker)
    if removed:
        logger.info(
            "agent_surfaces.outbound_retention.outbound_messages_pruned.observed",
            deleted_count=removed,
        )
