"""Resolve allocation-bound routes to the resident function runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import random

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
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpoint,
    FunctionRuntimeEndpointCache,
    FunctionRuntimeEndpointKey,
)
from app.modules.function.application.runtime_policy import (
    FUNCTION_JOB_CALLBACK_GRACE_SECONDS,
)
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionExecutionDispatch,
)


_FUNCTION_RUNTIME_PORT = 8090
AgentBoxClientFactory = Callable[[], AgentBoxClient]


class FunctionRuntimeRouteResolver:
    """Create and cache exact AgentBox grants for one pod runtime."""

    def __init__(
        self,
        *,
        agentbox_client_factory: AgentBoxClientFactory,
        endpoint_cache: FunctionRuntimeEndpointCache,
    ) -> None:
        self._agentbox_client_factory = agentbox_client_factory
        self._endpoint_cache = endpoint_cache
        self._profile = ProfileRef(
            name=settings.agentbox_function_profile_name,
            digest=settings.agentbox_function_profile_digest,
        )

    async def endpoint(
        self,
        dispatch: FunctionExecutionDispatch,
    ) -> FunctionRuntimeEndpoint:
        return await self._endpoint_cache.get(
            self._key(dispatch),
            loader=lambda: self._load(
                dispatch,
                deadline_at=dispatch.deadline_at,
            ),
        )

    async def control_endpoint(
        self,
        dispatch: FunctionExecutionDispatch,
    ) -> FunctionRuntimeEndpoint:
        """Resolve an existing allocation without creating a sandbox."""

        client = self._agentbox_client_factory()
        try:
            grant = await client.create_port_access(
                WorkloadKind.FUNCTION,
                dispatch.pod_id,
                _FUNCTION_RUNTIME_PORT,
                expires_at=max(
                    dispatch.deadline_at,
                    self._now() + timedelta(seconds=5),
                ),
            )
            return FunctionRuntimeEndpoint(
                url=grant.url,
                expires_at=grant.expires_at,
            )
        finally:
            await client.close()

    async def invalidate(
        self,
        dispatch: FunctionExecutionDispatch,
        endpoint: FunctionRuntimeEndpoint,
    ) -> None:
        await self._endpoint_cache.invalidate(
            self._key(dispatch),
            endpoint=endpoint,
        )

    async def _load(
        self,
        dispatch: FunctionExecutionDispatch,
        *,
        deadline_at: datetime,
    ) -> FunctionRuntimeEndpoint:
        client = self._agentbox_client_factory()
        try:
            await self._ensure_sandbox(
                client,
                dispatch,
                deadline_at=deadline_at,
            )
            grant = await client.create_port_access(
                WorkloadKind.FUNCTION,
                dispatch.pod_id,
                _FUNCTION_RUNTIME_PORT,
                expires_at=min(
                    deadline_at
                    + timedelta(seconds=FUNCTION_JOB_CALLBACK_GRACE_SECONDS),
                    self._now() + timedelta(hours=23, minutes=55),
                ),
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
        dispatch: FunctionExecutionDispatch,
        *,
        deadline_at: datetime,
    ) -> None:
        attempt = 0
        capacity_error: AgentBoxApiError | None = None
        while self._now() < deadline_at:
            try:
                handle = await client.ensure_sandbox(
                    WorkloadKind.FUNCTION,
                    dispatch.pod_id,
                    profile=self._profile,
                    admission_class=(
                        AdmissionClass.LATENCY
                        if dispatch.mode == FunctionDispatchMode.SYNCHRONOUS
                        else AdmissionClass.BATCH
                    ),
                    deadline_at=deadline_at,
                    verify_ready=True,
                )
            except AgentBoxApiError as exc:
                if str(getattr(exc, "code", "")).upper() == "CAPACITY_EXHAUSTED":
                    capacity_error = exc
                else:
                    capacity_error = None
                if exc.retry not in {
                    RetryDisposition.WAIT,
                    RetryDisposition.SAFE_SAME_OPERATION,
                }:
                    raise
                await self._wait_retry(
                    exc.retry_after_ms,
                    deadline_at,
                    attempt=attempt,
                )
                attempt += 1
                continue
            except httpx.TransportError:
                await self._wait_retry(None, deadline_at, attempt=attempt)
                attempt += 1
                continue
            if handle.ready:
                return
            await self._wait_retry(
                handle.retry_after_ms,
                deadline_at,
                attempt=attempt,
            )
            attempt += 1
        if capacity_error is not None:
            raise capacity_error
        raise TimeoutError("function sandbox was not ready before the deadline")

    def _key(
        self,
        dispatch: FunctionExecutionDispatch,
    ) -> FunctionRuntimeEndpointKey:
        return FunctionRuntimeEndpointKey(
            pod_id=dispatch.pod_id,
            profile_digest=self._profile.digest,
        )

    @staticmethod
    async def _wait_retry(
        retry_after_ms: int | None,
        deadline_at: datetime,
        *,
        attempt: int,
    ) -> None:
        remaining = (deadline_at - FunctionRuntimeRouteResolver._now()).total_seconds()
        if remaining <= 0:
            return
        server_floor = max(0.0, (retry_after_ms or 0) / 1000)
        backoff = min(5.0, 0.5 * (2 ** min(attempt, 4)))
        delay = max(server_floor, backoff) * random.uniform(1.0, 1.2)
        await asyncio.sleep(min(delay, remaining))

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
