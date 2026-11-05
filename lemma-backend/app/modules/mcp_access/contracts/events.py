"""What the pod MCP endpoint needs to serve `events/*`.

A submodule like the other contracts that reach a service: importing it pulls
the database layer, and `contracts/__init__` is imported by anything that wants
any contract at all.
"""

from __future__ import annotations

from pydantic import JsonValue

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.mcp_access.domain.entities import McpPrincipal
from app.modules.mcp_access.services.event_subscriptions import (
    EventSubscriptionError,
    EventSubscriptions,
    SubscribeRequest,
    can_read_table,
    list_events,
)

__all__ = [
    "EventSubscriptionError",
    "SubscribeRequest",
    "list_events",
    "subscribe_to_event",
    "unsubscribe_from_event",
]


def _subscriptions() -> EventSubscriptions:
    return EventSubscriptions(
        SessionUnitOfWorkFactory(async_session_maker), can_read_table=can_read_table
    )


async def subscribe_to_event(
    principal: McpPrincipal | None, request: SubscribeRequest
) -> dict[str, JsonValue]:
    return await _subscriptions().subscribe(principal, request)


async def unsubscribe_from_event(
    principal: McpPrincipal | None,
    *,
    name: str,
    arguments: dict[str, JsonValue],
    url: str,
) -> dict[str, JsonValue]:
    return await _subscriptions().unsubscribe(
        principal, name=name, arguments=arguments, url=url
    )
