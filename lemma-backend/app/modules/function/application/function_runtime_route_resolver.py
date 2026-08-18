"""Resolve allocation-bound routes to the resident function runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import random
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from opentelemetry import trace

from sandbox_runtime.protocol import (
    SandboxProfileRef,
    AdmissionClass,
    FunctionRuntimeLease,
    WorkloadKind,
)
from sandbox_runtime.errors import SandboxError, SandboxUnavailable
from app.modules.workspace.services.local_sandbox_client import (
    LocalSandboxClient,
)

from app.core.config import settings
from app.core.log.log import get_logger
from app.modules.workspace.config import workspace_settings
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


tracer = trace.get_tracer(__name__)
logger = get_logger(__name__)


def endpoint_reuse_seconds() -> int:
    """Reuse window, never allowed past the idle release that invalidates it.

    A cached endpoint outliving the sandbox behind it is not a stale-cache
    problem that costs a retry. Idle release *pauses* the sandbox -- that is how
    E2B persists the filesystem -- and an invocation against a paused sandbox
    fails at the transport layer, which `_invoke_runtime_with_recovery`
    deliberately never replays: a transport error cannot be told apart from one
    that already ran user code. So the run simply fails.

    The two settings live in different objects and are set by different
    deployments, and the shipped pair is 60 against a production-overridden 180
    while the field's own documentation still reasons about the 900 default.
    Halving is a margin, not a formula: the sweep runs on a cron, so release
    happens somewhere after its window, never before it.
    """
    configured = settings.function_runtime_endpoint_reuse_seconds
    idle_release = workspace_settings.idle_release_seconds
    if idle_release <= 0:
        return configured
    return max(5, min(configured, idle_release // 2))


SandboxClientFactory = Callable[[], LocalSandboxClient]
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
        sandbox_client_factory: SandboxClientFactory,
        endpoint_cache: FunctionRuntimeEndpointCache,
    ) -> None:
        self._sandbox_client_factory = sandbox_client_factory
        self._endpoint_cache = endpoint_cache
        self._profile = SandboxProfileRef(
            name=workspace_settings.function_profile_name,
            digest=workspace_settings.function_profile_digest,
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
                # was what kept idle function sandboxes alive: the sandbox runtime treats
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
        client = self._sandbox_client_factory()
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

    async def quarantine(
        self,
        pod_id: UUID,
        endpoint: FunctionRuntimeEndpoint,
    ) -> None:
        """Drop the endpoint *and* the sandbox behind it, after it stops serving.

        Dropping the cached endpoint alone does not help: the reload adopts the
        same sandbox, because adoption asks the provider whether the sandbox is
        running and a sandbox whose runtime process has died is still running.
        That is exactly what happened -- one slow cold start left a sandbox
        answering 502 on the runtime port, every later run was handed it, and
        the outage lasted 100 minutes until someone deleted it by hand. A
        freshly booted sandbox from the same template answered 404, so the
        template was sound and the process had died.

        Destroying is safe here in a way it would not be for a workspace: a
        function sandbox holds no user files, so replacing it costs a cold start
        and nothing else.
        """
        await self.invalidate_for(pod_id, endpoint)
        client = self._sandbox_client_factory()
        try:
            await client.destroy_sandbox(WorkloadKind.FUNCTION, pod_id)
        except SandboxError, httpx.HTTPError:
            # Best effort, and deliberately narrow: a provider that cannot be
            # reached leaves the endpoint evicted anyway, so the next run
            # re-resolves. Anything not from the sandbox or its transport is a
            # bug here and should surface rather than be swallowed on a path
            # that already has a failure in hand.
            logger.warning(
                "function.runtime.quarantine_failed",
                pod_id=str(pod_id),
                exc_info=True,
            )
        else:
            logger.info(
                "function.runtime.sandbox_quarantined",
                pod_id=str(pod_id),
            )
        finally:
            await client.close()

    async def _load(
        self,
        pod_id: UUID,
        *,
        admission_class: AdmissionClass,
        deadline_at: datetime,
        required_valid_until: datetime,
    ) -> FunctionRuntimeEndpoint:
        client = self._sandbox_client_factory()
        try:
            # Split because these fail and cost differently. `ensure_sandbox`
            # verifies readiness (a round trip to the sandbox, and a cold start
            # if there is none); the lease is control-plane bookkeeping. Every
            # endpoint-cache miss pays both, so which one dominates decides
            # whether the answer is a longer reuse window or a faster probe.
            with tracer.start_as_current_span(
                "lemma.function.runtime_endpoint.ensure_sandbox"
            ) as span:
                span.set_attribute("lemma.admission_class", admission_class.value)
                await self._ensure_sandbox(
                    client,
                    pod_id,
                    admission_class=admission_class,
                    deadline_at=deadline_at,
                )
            with tracer.start_as_current_span("lemma.function.runtime_endpoint.lease"):
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
            raise ValueError("the sandbox returned a mismatched function runtime lease")

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
                raise ValueError("the sandbox returned an invalid runtime request header")
            names.add(normalized)
            headers.append((item.name, item.value))
        return tuple(headers)

    async def _ensure_sandbox(
        self,
        client: LocalSandboxClient,
        pod_id: UUID,
        *,
        admission_class: AdmissionClass,
        deadline_at: datetime,
    ) -> None:
        attempt = 0
        last_failure: SandboxUnavailable | None = None
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
            except SandboxUnavailable as exc:
                # Retryable by type; anything definitive propagates. Keep the
                # last one so a timeout can say why the sandbox never came up
                # instead of only that it did not.
                last_failure = exc
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
        if last_failure is not None:
            raise last_failure
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
        # 100ms doubling to a 750ms ceiling, not 500ms doubling to 5s.
        #
        # This is a poll for "is the sandbox serving yet", and the old ladder
        # slept 0.5, 1, 2, 4, 5 -- so four waits was 7.5 to 9 seconds of pure
        # sleeping, on top of however long the sandbox actually took. A boot
        # that finishes at 2.1s was not noticed until 3.5s, and the overshoot
        # grew with every attempt. The caller is a user waiting on a synchronous
        # function call, so the cost of asking again is a cheap local check and
        # the cost of asking late is the whole request.
        #
        # The ceiling still matters: `retry_after_ms` from the provider is
        # honoured as a floor above it, so a sandbox that says "not for 5
        # seconds" is still believed.
        backoff = min(0.75, 0.1 * (2 ** min(attempt, 3)))
        delay = max(server_floor, backoff) * random.uniform(1.0, 1.2)
        await asyncio.sleep(min(delay, remaining))

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
