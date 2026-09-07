"""Running a catalog-curated profile operation, whatever kind the install is.

Composio goes through its own gateway; everything else goes through the same
`KindDispatcher` the execute-operation route uses. The split is not
decoration -- a third route used to exist here, a vendored client reached
through the legacy "LEMMA" provider, and it had no entry for any newer kind. So
an http-kind connector's profile fetch always threw, the caller's blanket
`except Exception` swallowed it, and every such account was stored with a null
email, display name and provider account id.

Split out of the connector service because it is a self-contained concern.
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
    if kind == ConnectorKind.COMPOSIO.value:
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


def normalize_profile_result(result: object, provider: str) -> dict | None:
    """One operation's answer, as a plain dict the identity readers understand.

    Composio wraps every tool execution result in
    ``{"data": ..., "successful": ..., "error": ...}``
    (``composio.tools.execute``'s ``ToolExecutionResponse``); the toolkit's
    actual fields -- email, name -- live one level down in ``data``, not at the
    top level. Every other kind answers with the provider's own body.
    """
    from app.modules.connectors.domain.connector import AuthProvider

    profile = _profile_to_dict(result)
    if isinstance(profile, dict) and provider.upper() == AuthProvider.COMPOSIO.value:
        unwrapped = profile.get("data")
        if isinstance(unwrapped, dict):
            return unwrapped
    return profile


def _profile_to_dict(profile: object) -> dict | None:
    """A provider's answer as a plain dict, whatever shape it arrived in."""
    if isinstance(profile, dict):
        return profile
    if hasattr(profile, "model_dump"):
        data = profile.model_dump(exclude_none=True, exclude_unset=True, mode="json")
        return data if isinstance(data, dict) else None
    return None
