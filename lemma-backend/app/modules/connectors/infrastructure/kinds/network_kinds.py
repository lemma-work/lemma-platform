"""The three tenant-configured kinds: ``http``, ``sql`` and ``mcp``.

These share a shape the brokered kinds do not: the *tenant* supplies the
endpoint. That makes install validation and URL/host vetting the load-bearing
part, so they live together rather than being copied three times.
"""

from __future__ import annotations

from typing import Any

from app.core.net.url_guard import UnsafeUrlError, assert_safe_host, assert_safe_url
from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.domain.connector import KindSpec
from app.modules.connectors.domain.errors import ConnectorValidationError
from app.modules.connectors.domain.kinds import (
    DiscoveredOperation,
    ExecutionRequest,
    ResolvedInstall,
)
from app.modules.connectors.infrastructure.adapters.mcp_executor import McpExecutor
from app.modules.connectors.infrastructure.adapters.openapi_http_executor import (
    OpenApiHttpExecutor,
)
from app.modules.connectors.infrastructure.adapters.sql_executor import SqlExecutor
from app.modules.connectors.infrastructure.kinds._install_validation import (
    validate_install_config,
)


class _TenantConfiguredInstaller:
    """Validates config, then vets every tenant-supplied network target.

    The vetting is the point. These three kinds are the only ones where the
    *tenant* chooses the address we connect to, so without it an org admin can
    aim an install at the cloud metadata service or at internal services on the
    cluster network and read the response back.
    """

    def __init__(
        self,
        *,
        url_fields: tuple[str, ...] = (),
        host_field: str | None = None,
        port_field: str = "port",
        default_port: int = 5432,
    ):
        self._url_fields = url_fields
        self._host_field = host_field
        self._port_field = port_field
        self._default_port = default_port

    async def validate_install(
        self,
        *,
        spec: KindSpec,
        config: dict[str, Any],
        config_source: AuthConfigSource,
    ) -> dict[str, Any]:
        validated = validate_install_config(spec, config, config_source)
        try:
            for field in self._url_fields:
                value = validated.get(field)
                if value:
                    await assert_safe_url(str(value))
            if self._host_field:
                host = validated.get(self._host_field)
                if host:
                    port = validated.get(self._port_field) or self._default_port
                    await assert_safe_host(str(host), int(port))
        except UnsafeUrlError as exc:
            raise ConnectorValidationError(
                str(exc), details={"reason": exc.reason}
            ) from exc
        return validated


class HttpKindExecutor:
    def __init__(self, executor: OpenApiHttpExecutor):
        self._executor = executor

    async def execute(self, request: ExecutionRequest) -> Any:
        return await self._executor.execute(
            connector_id=request.connector_id,
            operation_name=request.operation.execution_name,
            execution=request.operation.execution or {},
            payload=request.payload,
            third_party_credentials=request.credentials,
            connection_config=request.config,
            deadline_seconds=request.deadline_seconds,
        )


class SqlKindExecutor:
    def __init__(self, executor: SqlExecutor):
        self._executor = executor

    async def execute(self, request: ExecutionRequest) -> Any:
        return await self._executor.execute(
            connector_id=request.connector_id,
            operation_name=request.operation.execution_name,
            execution=request.operation.execution or {},
            payload=request.payload,
            third_party_credentials=request.credentials,
            connection_config=request.config,
        )


class McpKindExecutor:
    def __init__(self, executor: McpExecutor):
        self._executor = executor

    async def execute(self, request: ExecutionRequest) -> Any:
        return await self._executor.execute(
            connector_id=request.connector_id,
            operation_name=request.operation.execution_name,
            execution=request.operation.execution or {},
            payload=request.payload,
            third_party_credentials=request.credentials,
            connection_config=request.config,
            deadline_seconds=request.deadline_seconds,
        )


class OpenApiDiscoverer:
    """Discovers operations from a spec the install points at (or inlines).

    An `http` install need not carry a spec: a connector whose spec was bundled
    at catalog-import time (GitHub) has a static operation set living in the
    catalog. That is not a discovery failure, so it returns nothing rather than
    raising -- which it did, turning the create of any spec-less http install
    into a 500.
    """

    async def discover(
        self, install: ResolvedInstall, credentials: dict[str, Any] | None
    ) -> list[DiscoveredOperation]:
        from app.modules.connectors.services.discovery.openapi_discoverer import (
            discover_openapi,
        )

        config = install.config or {}
        if not (config.get("spec_url") or config.get("spec_inline")):
            return []

        found = await discover_openapi(
            connection_config=install.config, credentials=credentials
        )
        return [
            DiscoveredOperation(
                name=item.name,
                display_name=item.display_name,
                description=item.description,
                input_schema=item.input_schema,
                output_schema=item.output_schema,
                execution=item.execution,
            )
            for item in found
        ]


class McpDiscoverer:
    """Lists an MCP server's tools, bounded by the discovery timeout."""

    async def discover(
        self, install: ResolvedInstall, credentials: dict[str, Any] | None
    ) -> list[DiscoveredOperation]:
        from app.modules.connectors.services.discovery.mcp_discoverer import discover_mcp

        found = await discover_mcp(
            connection_config=install.config,
            credentials=credentials,
            timeout_seconds=connector_settings.connector_discovery_timeout_seconds,
        )
        return [
            DiscoveredOperation(
                name=item.name,
                display_name=item.display_name,
                description=item.description,
                input_schema=item.input_schema,
                output_schema=item.output_schema,
                execution=item.execution,
            )
            for item in found
        ]


def http_installer() -> _TenantConfiguredInstaller:
    return _TenantConfiguredInstaller(url_fields=("server_url", "spec_url"))


def sql_installer() -> _TenantConfiguredInstaller:
    return _TenantConfiguredInstaller(host_field="host")


def mcp_installer() -> _TenantConfiguredInstaller:
    return _TenantConfiguredInstaller(url_fields=("server_url",))


