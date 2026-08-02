"""The two kinds Lemma brokers rather than the tenant: ``composio`` and ``package``.

Neither takes a tenant-supplied endpoint -- Composio fronts the provider, and a
package is vendored code -- so there is no install target to vet and no
discovery: their operation sets come from the catalog.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.domain.connector import KindSpec, kind_to_provider
from app.modules.connectors.domain.errors import ConnectorValidationError
from app.modules.connectors.domain.kinds import ExecutionRequest
from app.modules.connectors.infrastructure.kinds._install_validation import (
    validate_install_config,
)


def _refresh_skew() -> timedelta:
    return timedelta(seconds=connector_settings.connector_credential_refresh_skew_seconds)


class ExpiryBasedRefresh:
    """Refresh only when the credential is actually near expiry.

    The previous path refreshed unconditionally before every execution, so each
    operation paid a full round trip to the provider before doing any work. A
    credential with no ``expires_at`` -- an API key, a bot token, a provider that
    reports no expiry -- is never proactively refreshed; a 401 at execution time
    drives the reactive path instead, which also catches server-side revocation
    that an expiry check never would.
    """

    def refresh_due(self, credentials: dict[str, Any], *, now: datetime) -> bool:
        expires_at = credentials.get("expires_at")
        if expires_at is None:
            return False
        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at)
            except ValueError:
                return False
        if not isinstance(expires_at, datetime):
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        reference = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        return expires_at <= reference + _refresh_skew()


class ComposioInstaller:
    async def validate_install(
        self,
        *,
        spec: KindSpec,
        config: dict[str, Any],
        config_source: AuthConfigSource,
    ) -> dict[str, Any]:
        if config_source == AuthConfigSource.ORG_CUSTOM:
            raise ConnectorValidationError(
                "Composio installs use Composio-managed credentials.",
                details={"reason": "org_custom_not_supported_for_composio"},
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
