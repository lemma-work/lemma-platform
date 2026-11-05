"""Keeping standing work's subscriptions to MCP servers alive.

A server grants a subscription until its `refreshBefore` and forgets it after.
Every five minutes, each one past halfway through what it was granted is
renewed by subscribing again with the same identity, which the draft defines
as a refresh rather than a second subscription.
"""

from __future__ import annotations

from datetime import timedelta

from app.core.infrastructure.work import WorkClass, periodic_job
from app.core.log.log import get_logger

logger = get_logger(__name__)


# Automation, not admin: it calls out to MCP servers.
@periodic_job(
    "connectors.mcp_subscriptions_renew",
    every=timedelta(minutes=5),
    cls=WorkClass.AUTOMATION,
)
async def renew_mcp_event_subscriptions() -> None:
    from app.modules.connectors.contracts.mcp_events import mcp_event_subscriptions

    renewed = await mcp_event_subscriptions().renew_due()
    if renewed:
        logger.info("connectors.mcp_events.renewed", renewed_count=renewed)
