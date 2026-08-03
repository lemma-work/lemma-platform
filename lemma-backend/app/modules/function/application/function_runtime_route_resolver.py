"""Resolve allocation-bound routes to the resident function runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import random
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from agentbox_client import (
    AdmissionClass,
    AgentBoxApiError,
    AgentBoxClient,
    FunctionRuntimeLease,
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


AgentBoxClientFactory = Callable[[], AgentBoxClient]
_RESERVED_APPLICATION_HEADERS = frozenset(
    {
        "authorization",
        "if-match",
        "prefer",
        "x-lemma-gateway-url",
    }
)
_HTTP_HEADER_TOKEN = frozenset(
    "!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)


class FunctionRuntimeRouteResolver:
    """Lease and cache the current provider's direct pod runtime endpoint."""

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
        required_valid_until = self._required_valid_until(dispatch)
        return await self.endpoint_for(
            dispatch.pod_id,
            admission_class=(
                AdmissionClass.LATENCY
                if dispatch.mode == FunctionDispatchMode.SYNCHRONOUS
                else AdmissionClass.BATCH
            ),
            deadline_at=dispatch.deadline_at,
            required_valid_until=required_valid_until,
        )

    async def endpoint_for(
        self,
        pod_id: UUID,
        *,
        admission_class: AdmissionClass,
        deadline_at: datetime,
        required_valid_until: datetime,
    ) -> FunctionRuntimeEndpoint:
        return await self._endpoint_cache.get(
            self._key(pod_id),
            required_valid_until=required_valid_until,
            wait_until=deadline_at,
            loader=lambda: self._load(
                pod_id,
                admission_class=admission_class,
                deadline_at=deadline_at,
                # Ask only for what this invocation needs, plus a short window
                # so a busy pod reuses one lease instead of paying a
                # control-plane call per call. Asking for a long horizon here
                # was what kept idle function sandboxes alive: AgentBox treats
                # a lease as activity, so a single invocation requesting a
                # four-hour endpoint protected its sandbox for four hours and
                # the five-minute idle destroy never fired. Function execution
                # is the activity, so the horizon must track invocations.
                required_valid_until=max(
                    required_valid_until,
                    self._now()
                    + timedelta(
                        seconds=settings.function_runtime_endpoint_reuse_seconds
                    ),
                ),
            ),
        )

    async def control_endpoint(
        self,
        dispatch: FunctionExecutionDispatch,
    ) -> FunctionRuntimeEndpoint:
        """Resolve an existing allocation without creating a sandbox."""

        now = self._now()
        deadline_at = now + timedelta(seconds=5)
        client = self._agentbox_client_factory()
        try:
            lease = await client.lease_function_runtime(
                dispatch.pod_id,
                required_valid_until=deadline_at,
                deadline_at=deadline_at,
            )
            return self._endpoint_from_lease(
                lease,
                pod_id=dispatch.pod_id,
                required_valid_until=deadline_at,
            )
        finally:
            await client.close()

    async def invalidate(
        self,
        dispatch: FunctionExecutionDispatch,
        endpoint: FunctionRuntimeEndpoint,
    ) -> None:
        await self.invalidate_for(dispatch.pod_id, endpoint)

    async def invalidate_for(
        self,
        pod_id: UUID,
        endpoint: FunctionRuntimeEndpoint,
    ) -> None:
        await self._endpoint_cache.invalidate(
            self._key(pod_id),
            endpoint=endpoint,
        )

    async def _load(
        self,
        pod_id: UUID,
        *,
        admission_class: AdmissionClass,
        deadline_at: datetime,
        required_valid_until: datetime,
    ) -> FunctionRuntimeEndpoint:
        client = self._agentbox_client_factory()
        try:
            await self._ensure_sandbox(
                client,
                pod_id,
                admission_class=admission_class,
                deadline_at=deadline_at,
            )
            lease = await client.lease_function_runtime(
                pod_id,
                required_valid_until=required_valid_until,
                deadline_at=deadline_at,
            )
            return self._endpoint_from_lease(
                lease,
                pod_id=pod_id,
                required_valid_until=required_valid_until,
            )
        finally:
            await client.close()

    def _endpoint_from_lease(
        self,
        lease: FunctionRuntimeLease,
        *,
        pod_id: UUID,
        required_valid_until: datetime,
    ) -> FunctionRuntimeEndpoint:
        self._validate_lease(
            lease,
            pod_id=pod_id,
            required_valid_until=required_valid_until,
        )
        return FunctionRuntimeEndpoint(
            url=lease.url.rstrip("/") + "/",
            request_headers=self._runtime_request_headers(lease),
            allocation_id=lease.allocation_id,
            allocation_epoch=lease.allocation_epoch,
            profile_digest=lease.profile.digest,
            expires_at=lease.expires_at,
        )

    def _validate_lease(
        self,
        lease: FunctionRuntimeLease,
        *,
        pod_id: UUID,
        required_valid_until: datetime,
    ) -> None:
        parsed = urlsplit(lease.url)
        lease_expiry_is_absolute = (
            lease.expires_at.tzinfo is not None
            and lease.expires_at.utcoffset() is not None
        )
        if (
            lease.logical_id != pod_id
            or lease.profile.name != self._profile.name
            or lease.profile.digest != self._profile.digest
            or lease.allocation_epoch < 1
            or not lease_expiry_is_absolute
            or lease.expires_at < required_valid_until
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("AgentBox returned a mismatched function runtime lease")

    @staticmethod
    def _runtime_request_headers(
        lease: FunctionRuntimeLease,
    ) -> tuple[tuple[str, str], ...]:
        headers: list[tuple[str, str]] = []
        names: set[str] = set()
        for item in lease.request_headers:
            normalized = item.name.lower()
            if (
                not item.name
                or any(character not in _HTTP_HEADER_TOKEN for character in item.name)
                or normalized in names
                or normalized in _RESERVED_APPLICATION_HEADERS
                or "\r" in item.value
                or "\n" in item.value
                or "\x00" in item.value
            ):
                raise ValueError("AgentBox returned an invalid runtime request header")
            names.add(normalized)
            headers.append((item.name, item.value))
        return tuple(headers)

    async def _ensure_sandbox(
        self,
        client: AgentBoxClient,
        pod_id: UUID,
        *,
        admission_class: AdmissionClass,
        deadline_at: datetime,
    ) -> None:
        attempt = 0
        capacity_error: AgentBoxApiError | None = None
        while self._now() < deadline_at:
            try:
                handle = await client.ensure_sandbox(
                    WorkloadKind.FUNCTION,
                    pod_id,
                    profile=self._profile,
                    admission_class=admission_class,
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
        pod_id: UUID,
    ) -> FunctionRuntimeEndpointKey:
        return FunctionRuntimeEndpointKey(
            pod_id=pod_id,
            profile_digest=self._profile.digest,
        )

    @staticmethod
    def _required_valid_until(
        dispatch: FunctionExecutionDispatch,
    ) -> datetime:
        required_valid_until = dispatch.deadline_at
        if dispatch.mode == FunctionDispatchMode.ASYNCHRONOUS:
            required_valid_until += timedelta(
                seconds=FUNCTION_JOB_CALLBACK_GRACE_SECONDS
            )
        return required_valid_until

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
