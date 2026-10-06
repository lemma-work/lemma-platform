"""Listening to a connected MCP server's events, as `schedule` uses it.

Four operations. A schedule on an MCP server's event is a WEBHOOK schedule
whose config names `source: "mcp"`, the event and its arguments; creating it
subscribes on the author's account and stores our subscription id as its
`provider_trigger_id`, which is also the routing key its deliveries carry --
the same shape a Composio trigger has, so editing and deleting go the way they
already do. `mcp_event_offers` is what the "When…" picker lists.

A submodule, like `triggers.py`: it pulls the model layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import JsonValue

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.connectors.domain.account import AccountStatus
from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.domain.errors import (
    ConnectorDomainError,
    ConnectorInfrastructureError,
    ConnectorValidationError,
    CredentialsNotFoundError,
)
from app.modules.connectors.domain.mcp_events import MCP_WEBHOOK_SOURCE
from app.modules.connectors.infrastructure.adapters.mcp_executor import (
    build_mcp_headers,
)
from app.modules.connectors.infrastructure.repositories.mcp_event_repository import (
    McpEventRepository,
)
from app.modules.connectors.services.mcp_event_subscriptions import (
    McpEventSubscriptions,
    McpTarget,
)


@dataclass(frozen=True, slots=True)
class McpEventOffer:
    """One event a person could start standing work on."""

    account_id: UUID
    server: str
    name: str
    description: str | None
    input_schema: dict[str, JsonValue]
    payload_schema: dict[str, JsonValue]


async def mcp_target(
    uow: SqlAlchemyUnitOfWork, account_id: UUID, user_id: UUID
) -> McpTarget:
    """The server behind one of this person's connected MCP accounts."""
    from app.modules.connectors.api.dependencies import get_connector_service

    service = get_connector_service(uow)
    account = await service.get_account(account_id, user_id)
    if account.status is not AccountStatus.CONNECTED:
        raise ConnectorValidationError("That account is not connected.")
    install = await service.auth_config_repository.get(account.auth_config_id)
    kind = getattr(install.kind, "value", install.kind) if install else None
    if install is None or kind != ConnectorKind.MCP.value:
        raise ConnectorValidationError(
            "Only a connected MCP server's events can be listened to."
        )
    config = install.config or {}
    server_url = config.get("server_url")
    if not isinstance(server_url, str) or not server_url:
        raise ConnectorValidationError("That MCP server has no address.")
    try:
        credentials = (
            await service.get_account_credentials(
                account_id, user_id, account.organization_id
            )
        ).model_dump(exclude_none=True)
    except CredentialsNotFoundError:
        # A server that asks for no auth: the account holds nothing.
        credentials = {}
    # A refreshed OAuth token is written back; keep it.
    await uow.commit()
    return McpTarget(
        server_url=server_url,
        headers=build_mcp_headers(config, credentials),
        auth_config_id=install.id,
        organization_id=account.organization_id,
    )


def mcp_event_subscriptions() -> McpEventSubscriptions:
    return McpEventSubscriptions(
        SessionUnitOfWorkFactory(async_session_maker), target=mcp_target
    )


async def subscribe_to_mcp_event(
    *,
    account_id: UUID,
    user_id: UUID,
    event: str,
    arguments: dict[str, JsonValue],
) -> str:
    """Subscribe; our subscription id, which routes its deliveries."""
    subscription_id = await mcp_event_subscriptions().subscribe(
        account_id=account_id, user_id=user_id, event=event, arguments=arguments
    )
    return str(subscription_id)


async def unsubscribe_from_mcp_event(subscription_id: str) -> None:
    try:
        parsed = UUID(subscription_id)
    except ValueError:
        return
    await mcp_event_subscriptions().unsubscribe(parsed)


async def mcp_event_offers(
    uow: SqlAlchemyUnitOfWork, *, user_id: UUID, organization_id: UUID
) -> list[McpEventOffer]:
    offers = await McpEventRepository(uow.session).offers_for(
        user_id=user_id, organization_id=organization_id
    )
    return [
        McpEventOffer(
            account_id=offer.account_id,
            server=offer.install_name,
            name=offer.event.name,
            description=offer.event.description,
            input_schema=offer.event.input_schema,
            payload_schema=offer.event.payload_schema,
        )
        for offer in offers
    ]


__all__ = [
    "ConnectorDomainError",
    "ConnectorInfrastructureError",
    "MCP_WEBHOOK_SOURCE",
    "McpEventOffer",
    "mcp_event_offers",
    "subscribe_to_mcp_event",
    "unsubscribe_from_mcp_event",
]
