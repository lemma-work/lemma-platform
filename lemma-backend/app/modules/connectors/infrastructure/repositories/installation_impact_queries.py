"""The MCP event subscriptions held through one installation."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.connectors.infrastructure.models.mcp_event import (
    ConnectorEventSubscription,
)


async def event_subscription_count(session: AsyncSession, auth_config_id: UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(ConnectorEventSubscription)
            .where(ConnectorEventSubscription.auth_config_id == auth_config_id)
        )
        or 0
    )


async def event_subscription_ids(
    session: AsyncSession, auth_config_id: UUID, *, limit: int
) -> set[str]:
    """Our ids for them -- what a schedule holds as ``provider_trigger_id``."""
    rows = await session.scalars(
        select(ConnectorEventSubscription.id)
        .where(ConnectorEventSubscription.auth_config_id == auth_config_id)
        .limit(limit)
    )
    return {str(one) for one in rows}


__all__ = ["event_subscription_count", "event_subscription_ids"]
