"""Schema inspection through the pod-scoped resident function runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from uuid import UUID

import httpx

from sandbox_runtime.protocol import AdmissionClass

from app.modules.function.config import function_settings
from app.core.config import settings
from app.modules.function.application.function_session_token_cache import (
    FunctionSessionToken,
    FunctionSessionTokenCache,
    FunctionSessionTokenKey,
)
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpoint,
    FunctionRuntimeEndpointCache,
)
from app.modules.function.application.function_runtime_route_resolver import (
    SandboxClientFactory,
    FunctionRuntimeRouteResolver,
)
from app.modules.function.contracts.runtime import RuntimeSchemaInspection
from app.modules.function.domain.entities import (
    FunctionArtifact,
    FunctionSchemaSet,
)
from app.modules.function.domain.errors import FunctionValidationError


RuntimeHttpClientFactory = Callable[[], httpx.AsyncClient]
TokenMinter = Callable[..., Awaitable[FunctionSessionToken]]


class FunctionSchemaDispatcher:
    """Build-plane client for the stateless resident function runtime."""

    def __init__(
        self,
        *,
        sandbox_client_factory: SandboxClientFactory,
        token_minter: TokenMinter,
        token_cache: FunctionSessionTokenCache,
        endpoint_cache: FunctionRuntimeEndpointCache,
        runtime_http_client_factory: RuntimeHttpClientFactory,
        delegated_tokens_enabled: bool,
    ) -> None:
        self._token_minter = token_minter
        self._token_cache = token_cache
        self._routes = FunctionRuntimeRouteResolver(
            sandbox_client_factory=sandbox_client_factory,
            endpoint_cache=endpoint_cache,
        )
        self._runtime_http_client_factory = runtime_http_client_factory
        self._delegated_tokens_enabled = delegated_tokens_enabled

    async def extract_schemas(
        self,
        *,
        function_id: UUID,
        pod_id: UUID,
        user_id: UUID,
        function_name: str,
        artifact: FunctionArtifact,
    ) -> FunctionSchemaSet:
        deadline_at = self._now() + timedelta(
            seconds=function_settings.function_api_deadline_seconds
        )
        runtime = self._runtime_http_client_factory()
        function_token = await self._token_cache.get(
            FunctionSessionTokenKey(
                user_id=user_id,
                pod_id=pod_id,
                function_id=function_id,
                revision_hash=artifact.revision_hash,
                workload_name=function_name,
                scope=(),
                delegated_tokens_enabled=self._delegated_tokens_enabled,
            ),
            minter=self._token_minter,
            min_validity_until=deadline_at,
        )
        response: httpx.Response | None = None
        last_transport_error: httpx.TransportError | None = None
        for attempt in range(2):
            endpoint = await self._runtime_endpoint(
                pod_id,
                deadline_at=deadline_at,
            )
            remaining = max(1, (deadline_at - self._now()).total_seconds())
            try:
                response = await runtime.post(
                    urljoin(endpoint.url, f"functions/{function_id}/schemas"),
                    headers={
                        **endpoint.headers(),
                        "Authorization": f"Bearer {function_token.value}",
                        "If-Match": f'"{artifact.revision_hash}"',
                        "X-Lemma-Gateway-Url": self._runtime_gateway_url(),
                    },
                    timeout=httpx.Timeout(10, read=remaining),
                )
            except httpx.TransportError as exc:
                response = None
                last_transport_error = exc
                await self._invalidate(pod_id, endpoint)
                if attempt == 0:
                    continue
                break
            if response.status_code in {404, 410} or response.status_code >= 500:
                await self._invalidate(pod_id, endpoint)
                if attempt == 0:
                    continue
            break
        return self._validated_schemas(
            response,
            transport_error=last_transport_error,
        )

    async def _runtime_endpoint(
        self,
        pod_id: UUID,
        *,
        deadline_at: datetime,
    ) -> FunctionRuntimeEndpoint:
        return await self._routes.endpoint_for(
            pod_id,
            admission_class=AdmissionClass.LATENCY,
            deadline_at=deadline_at,
            required_valid_until=deadline_at,
        )

    async def _invalidate(
        self,
        pod_id: UUID,
        endpoint: FunctionRuntimeEndpoint,
    ) -> None:
        await self._routes.invalidate_for(pod_id, endpoint)

    @staticmethod
    def _validated_schemas(
        response: httpx.Response | None,
        *,
        transport_error: httpx.TransportError | None,
    ) -> FunctionSchemaSet:
        details = {"stage": "schema_extraction"}
        if response is None:
            raise FunctionValidationError(
                "Function schema inspection runtime is unavailable",
                details=details,
            ) from transport_error
        if response.status_code not in {200, 422}:
            raise FunctionValidationError(
                "Function schema inspection runtime rejected the artifact",
                details=details,
            )
        try:
            inspection = RuntimeSchemaInspection.model_validate(response.json())
        except ValueError as exc:
            raise FunctionValidationError(
                "Function schema inspection returned an invalid response",
                details=details,
            ) from exc
        if response.status_code != 200 or not inspection.ok:
            error = inspection.error
            message = "Function schema extraction failed"
            if error is not None:
                message = f"{message}: {error.name}: {error.message}"
            raise FunctionValidationError(message, details=details)
        assert inspection.schemas is not None
        return FunctionSchemaSet(
            input=inspection.schemas.input,
            output=inspection.schemas.output,
            config=inspection.schemas.config,
        )

    @staticmethod
    def _runtime_gateway_url() -> str:
        configured = function_settings.function_runtime_gateway_url or settings.api_url
        return configured.rstrip("/")

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
