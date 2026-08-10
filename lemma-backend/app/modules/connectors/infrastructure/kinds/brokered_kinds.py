"""The two kinds Lemma brokers rather than the tenant: ``composio`` and ``package``.

Neither takes a tenant-supplied endpoint -- Composio fronts the provider, and a
package is vendored code -- so there is no install target to vet and no
discovery: their operation sets come from the catalog.
"""

from __future__ import annotations

from typing import Any

from app.modules.connectors.domain.auth_config import (
    COMPOSIO_ORG_CUSTOM_REASON,
    COMPOSIO_SYSTEM_CREDENTIALS_ONLY,
    AuthConfigSource,
)
from app.modules.connectors.domain.connector import KindSpec, kind_to_provider
from app.modules.connectors.domain.errors import ConnectorValidationError
from app.modules.connectors.domain.kinds import ExecutionRequest
from app.modules.connectors.infrastructure.kinds._install_validation import (
    validate_install_config,
)


class ComposioInstaller:
    async def validate_install(
        self,
        *,
        spec: KindSpec,
        config: dict[str, Any],
        config_source: AuthConfigSource,
    ) -> dict[str, Any]:
        # Also reached on the update path, which the service-layer check in
        # `_validate_auth_config_request` never sees -- so this is a second
        # guard, not a duplicate one. Both raise the same shared message.
        if config_source == AuthConfigSource.ORG_CUSTOM:
            raise ConnectorValidationError(
                COMPOSIO_SYSTEM_CREDENTIALS_ONLY,
                details={"reason": COMPOSIO_ORG_CUSTOM_REASON},
            )
        return validate_install_config(spec, config, config_source)


class PackageInstaller:
    async def validate_install(
        self,
        *,
        spec: KindSpec,
        config: dict[str, Any],
        config_source: AuthConfigSource,
    ) -> dict[str, Any]:
        return validate_install_config(spec, config, config_source)


class ComposioKindExecutor:
    """Runs a Composio tool. The SDK is synchronous and is offloaded inside.

    ``provider`` is passed explicitly. The gateway this delegates to still routes
    on the legacy vocabulary and defaults to LEMMA when it is absent -- so
    omitting it silently sent every Composio call down the vendored-package path,
    where the operation does not exist.
    """

    def __init__(self, gateway: Any):
        self._gateway = gateway

    async def execute(self, request: ExecutionRequest) -> Any:
        return await self._gateway.execute_operation(
            connector_id=request.connector_id,
            operation_name=request.operation.execution_name,
            payload=request.payload,
            third_party_credentials=request.credentials,
            provider=kind_to_provider(request.kind).value,
        )


class PackageKindExecutor:
    """Runs an operation through the vendored ``lemma-connectors`` client."""

    def __init__(self, gateway: Any):
        self._gateway = gateway

    async def execute(self, request: ExecutionRequest) -> Any:
        return await self._gateway.execute_operation(
            connector_id=request.connector_id,
            operation_name=request.operation.execution_name,
            payload=request.payload,
            third_party_credentials=request.credentials,
            auth_token=request.auth_token,
            api_url=request.api_url,
            provider=kind_to_provider(request.kind).value,
        )
