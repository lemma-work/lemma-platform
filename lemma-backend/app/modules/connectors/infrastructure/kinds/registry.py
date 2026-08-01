"""The one place that maps a kind to its implementation.

Dispatch used to happen twice: the provider chose a gateway, then the gateway
looked at the operation's descriptor to choose an executor. A single lookup here
replaces both, so adding a kind means adding one registry entry rather than
touching a gateway, a provider registry and a descriptor switch.
"""

from __future__ import annotations

from typing import Any

from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.domain.errors import ConnectorValidationError
from app.modules.connectors.domain.kinds import KindPlugin
from app.modules.connectors.infrastructure.adapters.mcp_executor import McpExecutor
from app.modules.connectors.infrastructure.adapters.openapi_http_executor import (
    OpenApiHttpExecutor,
)
from app.modules.connectors.infrastructure.adapters.sql_executor import (
    shared_sql_executor,
)
from app.modules.connectors.infrastructure.kinds.brokered_kinds import (
    ComposioInstaller,
    ComposioKindExecutor,
    ExpiryBasedRefresh,
    PackageInstaller,
    PackageKindExecutor,
)
from app.modules.connectors.infrastructure.kinds.network_kinds import (
    HttpKindExecutor,
    McpDiscoverer,
    McpKindExecutor,
    OpenApiDiscoverer,
    SqlKindExecutor,
    http_installer,
    mcp_installer,
    never_refreshes,
    sql_installer,
)


class KindRegistry:
    """Resolves a kind to its plugin."""

    def __init__(self, plugins: dict[ConnectorKind, KindPlugin]):
        self._plugins = dict(plugins)

    def get(self, kind: ConnectorKind | str) -> KindPlugin:
        if isinstance(kind, ConnectorKind):
            resolved = kind
        else:
            try:
                resolved = ConnectorKind(kind)
            except ValueError as exc:
                # A stored kind this build does not know about (a rollback, or a
                # hand-edited row). Surface it as a domain error rather than
                # letting a raw ValueError become a 500.
                raise ConnectorValidationError(
                    "Unknown connector kind.",
                    details={"reason": "unknown_kind"},
                ) from exc
        plugin = self._plugins.get(resolved)
        if plugin is None:
            raise ConnectorValidationError(
                f"No implementation registered for connector kind '{resolved.value}'.",
                details={"reason": "unknown_kind"},
            )
        return plugin

    def kinds(self) -> list[ConnectorKind]:
        return list(self._plugins)

    def __contains__(self, kind: object) -> bool:
        try:
            return ConnectorKind(kind) in self._plugins  # type: ignore[arg-type]
        except (ValueError, TypeError):
            return False


def build_kind_registry(
    *,
    composio_gateway: Any,
    package_gateway: Any,
    http_executor: OpenApiHttpExecutor | None = None,
    mcp_executor: McpExecutor | None = None,
) -> KindRegistry:
    """Construct the registry with its process-wide collaborators.

    The SQL executor is deliberately the process-shared instance: it owns engine
    pools against customer databases, and a per-request instance would open and
    abandon a pool on every call.
    """
    http = http_executor or OpenApiHttpExecutor()
    mcp = mcp_executor or McpExecutor()

    return KindRegistry(
        {
            ConnectorKind.COMPOSIO: KindPlugin(
                kind=ConnectorKind.COMPOSIO,
                executor=ComposioKindExecutor(composio_gateway),
                installer=ComposioInstaller(),
                authenticator=ExpiryBasedRefresh(),
            ),
            ConnectorKind.PACKAGE: KindPlugin(
                kind=ConnectorKind.PACKAGE,
                executor=PackageKindExecutor(package_gateway),
                installer=PackageInstaller(),
                authenticator=ExpiryBasedRefresh(),
            ),
            ConnectorKind.HTTP: KindPlugin(
                kind=ConnectorKind.HTTP,
                executor=HttpKindExecutor(http),
                installer=http_installer(),
                discoverer=OpenApiDiscoverer(),
                authenticator=ExpiryBasedRefresh(),
            ),
            ConnectorKind.SQL: KindPlugin(
                kind=ConnectorKind.SQL,
                executor=SqlKindExecutor(shared_sql_executor()),
                installer=sql_installer(),
                authenticator=never_refreshes(),
            ),
            ConnectorKind.MCP: KindPlugin(
                kind=ConnectorKind.MCP,
                executor=McpKindExecutor(mcp),
                installer=mcp_installer(),
                discoverer=McpDiscoverer(),
                authenticator=never_refreshes(),
            ),
        }
    )
