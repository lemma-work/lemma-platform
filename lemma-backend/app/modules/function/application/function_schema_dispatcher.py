"""Schema inspection through the pod-scoped resident function runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from uuid import UUID

import httpx

from agentbox_client import (
    AdmissionClass,
    AgentBoxApiError,
    AgentBoxClient,
    ProfileRef,
    RetryDisposition,
    WorkloadKind,
)

from app.core.config import settings
from app.modules.function.application.function_runtime_credentials import (
    FunctionRuntimeCapabilitySigner,
)
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpoint,
    FunctionRuntimeEndpointCache,
    FunctionRuntimeEndpointKey,
)
from app.modules.function.contracts.runtime import RuntimeSchemaInspection
from app.modules.function.domain.entities import (
    FunctionArtifact,
    FunctionSchemaSet,
)
from app.modules.function.domain.errors import FunctionValidationError


_FUNCTION_RUNTIME_PORT = 8090

AgentBoxClientFactory = Callable[[], AgentBoxClient]
RuntimeHttpClientFactory = Callable[[], httpx.AsyncClient]


class FunctionSchemaDispatcher:
    """Build-plane client for the stateless resident function runtime."""

    def __init__(
        self,
        *,
        credential_signer: FunctionRuntimeCapabilitySigner,
        agentbox_client_factory: AgentBoxClientFactory,
        endpoint_cache: FunctionRuntimeEndpointCache,
        runtime_http_client_factory: RuntimeHttpClientFactory,
    ) -> None:
        self._signer = credential_signer
        self._agentbox_client_factory = agentbox_client_factory
        self._endpoint_cache = endpoint_cache
        self._runtime_http_client_factory = runtime_http_client_factory
        self._profile = ProfileRef(
            name=settings.agentbox_function_profile_name,
            digest=settings.agentbox_function_profile_digest,
        )

    async def extract_schemas(
        self,
        *,
        function_id: UUID,
        pod_id: UUID,
        artifact: FunctionArtifact,
    ) -> FunctionSchemaSet:
        deadline_at = self._now() + timedelta(
            seconds=settings.function_api_deadline_seconds
        )
        runtime = self._runtime_http_client_factory()
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
                        "Authorization": (
                            "Bearer "
                            + self._signer.derive_compilation(
                                function_id,
                                artifact.revision_hash,
                            )
                        ),
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
        return await self._endpoint_cache.get(
            self._endpoint_key(pod_id),
            loader=lambda: self._load_runtime_endpoint(
                pod_id,
                deadline_at=deadline_at,
            ),
        )

    async def _load_runtime_endpoint(
        self,
        pod_id: UUID,
        *,
        deadline_at: datetime,
    ) -> FunctionRuntimeEndpoint:
        client = self._agentbox_client_factory()
        try:
            await self._ensure_sandbox(
                client,
                pod_id=pod_id,
                deadline_at=deadline_at,
            )
            grant = await client.create_port_access(
                WorkloadKind.FUNCTION,
                pod_id,
                _FUNCTION_RUNTIME_PORT,
                expires_at=self._port_access_expiry(deadline_at),
            )
            return FunctionRuntimeEndpoint(
                url=grant.url,
                expires_at=grant.expires_at,
            )
        finally:
            await client.close()

    async def _ensure_sandbox(
        self,
        client: AgentBoxClient,
        *,
        pod_id: UUID,
        deadline_at: datetime,
    ) -> None:
        while self._now() < deadline_at:
            try:
                handle = await client.ensure_sandbox(
                    WorkloadKind.FUNCTION,
                    pod_id,
                    profile=self._profile,
                    admission_class=AdmissionClass.LATENCY,
                    deadline_at=deadline_at,
                )
            except AgentBoxApiError as exc:
                if exc.retry not in {
                    RetryDisposition.WAIT,
                    RetryDisposition.SAFE_SAME_OPERATION,
                }:
                    raise
                await self._wait_retry(exc.retry_after_ms, deadline_at)
                continue
            except httpx.TransportError:
                await self._wait_retry(None, deadline_at)
                continue
            if handle.ready:
                return
            await self._wait_retry(handle.retry_after_ms, deadline_at)
        raise TimeoutError("function sandbox was not ready before the deadline")

    async def _invalidate(
        self,
        pod_id: UUID,
        endpoint: FunctionRuntimeEndpoint,
    ) -> None:
        await self._endpoint_cache.invalidate(
            self._endpoint_key(pod_id),
            endpoint=endpoint,
        )

    def _endpoint_key(self, pod_id: UUID) -> FunctionRuntimeEndpointKey:
        return FunctionRuntimeEndpointKey(
            pod_id=pod_id,
            profile_digest=self._profile.digest,
        )

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
    async def _wait_retry(
        retry_after_ms: int | None,
        deadline_at: datetime,
    ) -> None:
        remaining = (deadline_at - FunctionSchemaDispatcher._now()).total_seconds()
        if remaining <= 0:
            return
        delay = max(0.05, (retry_after_ms or 200) / 1000)
        await asyncio.sleep(min(delay, remaining))

    @staticmethod
    def _runtime_gateway_url() -> str:
        configured = settings.function_runtime_gateway_url or settings.api_url
        return configured.rstrip("/")

    @staticmethod
    def _port_access_expiry(deadline_at: datetime) -> datetime:
        return min(
            deadline_at + timedelta(seconds=10),
            FunctionSchemaDispatcher._now() + timedelta(hours=23, minutes=55),
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
