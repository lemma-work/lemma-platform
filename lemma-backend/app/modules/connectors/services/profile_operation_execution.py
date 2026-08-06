"""Routing a catalog-curated profile operation to the right connector kind.

`RoutingOperationGateway` only understands two routes -- Composio, and the
legacy vendored-package client for "LEMMA" -- because it predates the
http/sql/mcp kind framework. That's fine for package-kind connectors (Gmail,
Slack, Jira all have a vendored client), but silently fails for any newer
kind's profile operation (e.g. an http-kind connector like GitHub): the
legacy client has no entry for it, so the profile fetch always threw and was
swallowed by the caller's blanket `except Exception`, leaving every such
account's email/display_name/provider_account_id permanently null. Composio
and package keep the existing, proven path; http/sql/mcp route through the
same KindDispatcher the execute-operation route itself uses.

Split out of the connector service, mirroring `account_profile.py`, because
it is a self-contained concern.
"""

from __future__ import annotations

from typing import Any, Callable

from app.modules.connectors.domain.account import OAuthCredentials
from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.domain.ports import AppOperationGatewayPort


async def execute_profile_operation(
    *,
    connector_id: str,
    kind: str,
    operation: Any,
    provider: str,
    credentials: OAuthCredentials,
    operation_gateway: AppOperationGatewayPort,
    get_dispatcher: Callable[[], Any],
) -> Any:
    third_party_credentials = credentials.model_dump(exclude_none=True)
    if kind in (ConnectorKind.PACKAGE.value, ConnectorKind.COMPOSIO.value):
        return await operation_gateway.execute_operation(
            connector_id=connector_id,
            operation_name=operation.execution_name,
            payload={},
            third_party_credentials=third_party_credentials,
            provider=provider,
        )
    from app.modules.connectors.domain.connector_operation import ResolvedOperation

    dispatcher = get_dispatcher()
    request = dispatcher.build_request(
        connector_id=connector_id,
        kind=ConnectorKind(kind),
        operation=ResolvedOperation(
            name=operation.name,
            provider_operation_name=operation.execution_name,
            input_schema=operation.input_schema,
            execution=operation.execution,
        ),
        payload={},
        credentials=third_party_credentials,
        config={},
    )
    return await dispatcher.execute(request)
