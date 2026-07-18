from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import random
from dataclasses import dataclass
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

import httpx

from agentbox.apps import SANDBOX_APPS, SandboxAppSpec
from agentbox.schemas import (
    SandboxEnsureRequest,
    SandboxInternalAppStatus,
    SandboxInternalStatus,
)

from .cloud_config import E2BProviderConfig
from .errors import ProviderError, SandboxNotFoundError
from .legacy import LegacyRuntimeProviderMixin
from .models import (
    EndpointProtocol,
    ManagedSandbox,
    ProviderCapabilities,
    ProviderCapacityPolicy,
    SandboxEndpoint,
    SandboxRef,
)


from agentbox.observability import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class _E2BSdk:
    sandbox_cls: Any
    query_cls: Any
    rate_limit_error: type[Exception]
    not_found_error: type[Exception]
    sandbox_error: type[Exception]
    urlopen: Any = urlrequest.urlopen
    definitive_create_errors: tuple[type[Exception], ...] = ()


@dataclass(frozen=True)
class _E2BManagedInfo:
    provider_id: str
    metadata: dict[str, str]
    state: str


def _load_sdk() -> _E2BSdk:
    try:
        from e2b import (
            AsyncSandbox,
            AuthenticationException,
            InvalidArgumentException,
            RateLimitException,
            SandboxException,
            SandboxNotFoundException,
            SandboxQuery,
            TemplateException,
        )
    except ImportError as exc:  # pragma: no cover - installation diagnostic
        raise RuntimeError(
            "AGENTBOX_PROVIDER=e2b requires the 'agentbox[e2b]' extra"
        ) from exc
    return _E2BSdk(
        sandbox_cls=AsyncSandbox,
        query_cls=SandboxQuery,
        rate_limit_error=RateLimitException,
        not_found_error=SandboxNotFoundException,
        sandbox_error=SandboxException,
        definitive_create_errors=(
            AuthenticationException,
            InvalidArgumentException,
            TemplateException,
        ),
    )


class E2BSandboxProvider(LegacyRuntimeProviderMixin):
    """E2B compute adapter; all runtime and app transport remains in core."""

    provider_name = "e2b"
    capabilities = ProviderCapabilities(
        stable_release_identity=True,
        release_preserves_filesystem=True,
        private_egress_isolation=True,
        authenticated_http=True,
        authenticated_websocket=True,
    )

    def __init__(
        self,
        config: E2BProviderConfig | None = None,
        *,
        sdk: _E2BSdk | None = None,
    ) -> None:
        self.config = config or E2BProviderConfig.from_env()
        self._sdk = sdk or _load_sdk()
        # Capacity is atomic inside one manager process. The manager lifespan
        # rejects multiple replicas until reservations become distributed.
        self._create_locks: dict[str, asyncio.Lock] = {}
        self._lease_renewal_locks: dict[str, asyncio.Lock] = {}
        self._lease_renewed_at: dict[str, float] = {}
        self._list_lock = asyncio.Lock()
        self._sandboxes: dict[str, object] = {}
        self._known_infos: dict[str, _E2BManagedInfo] = {}
        self._capacity_condition = asyncio.Condition()
        self._capacity_init_lock = asyncio.Lock()
        self._capacity_reservations: set[str] = set()
        self._ambiguous_capacity_reservations: set[str] = set()
        self._counted_provider_ids: set[str] = set()
        self._capacity_generation = 0
        self._observed_active_count: int | None = None
        self._create_semaphore = asyncio.Semaphore(self.config.create_max_in_flight)
        self._create_in_flight = 0
        self._create_rate_lock = asyncio.Lock()
        self._next_create_at = 0.0

    @property
    def capacity_policy(self) -> ProviderCapacityPolicy:
        return ProviderCapacityPolicy(
            scope=(
                f"{self.provider_name}:{self.config.owner}:{self.config.environment}"
            ),
            max_active=self.config.max_active,
        )

    def _api_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "api_key": self.config.api_key,
            "request_timeout": self.config.request_timeout_seconds,
        }
        if self.config.api_url:
            options["api_url"] = self.config.api_url
        if self.config.domain:
            options["domain"] = self.config.domain
        return options

    def _metadata(
        self,
        sandbox_id: str | None = None,
        generation_token: str | None = None,
    ) -> dict[str, str]:
        metadata = {
            # ``managed-by`` is the outermost inventory fence. Owner and
            # environment remain mandatory inner scopes, but older AgentBox
            # managers queried only this key. A versioned value lets a new
            # deployment share an E2B team/API key with that legacy manager
            # without either reconciler seeing or purging the other's
            # sandboxes during a rolling migration. Environment-specific
            # values such as ``agentbox-dev`` and ``agentbox-prod`` are
            # recommended when environments share an E2B team.
            "managed-by": self.config.managed_by,
            "agentbox-owner": self.config.owner,
            "agentbox-environment": self.config.environment,
        }
        if sandbox_id:
            metadata["agentbox-id"] = sandbox_id
        if generation_token:
            metadata["agentbox-generation"] = generation_token
        return metadata

    @staticmethod
    def _provider_retry_after(exc: Exception) -> float | None:
        candidates = [
            getattr(exc, "retry_after", None),
            getattr(exc, "retry_after_seconds", None),
        ]
        headers = getattr(exc, "headers", None)
        response = getattr(exc, "response", None)
        if headers is None and response is not None:
            headers = getattr(response, "headers", None)
        if headers is not None:
            try:
                candidates.extend(
                    [headers.get("Retry-After"), headers.get("retry-after")]
                )
            except AttributeError:
                pass
        for value in candidates:
            if value is None:
                continue
            if hasattr(value, "total_seconds"):
                value = value.total_seconds()
            try:
                seconds = float(value)
            except (TypeError, ValueError):
                try:
                    retry_at = parsedate_to_datetime(str(value))
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    continue
            return max(seconds, 0.0)
        return None

    async def _sleep(self, delay: float) -> None:
        await asyncio.sleep(delay)

    def _clock(self) -> float:
        return asyncio.get_running_loop().time()

    async def _with_rate_limit_retry(self, operation):  # type: ignore[no-untyped-def]
        fallback_delay = 0.5
        for attempt in range(6):
            try:
                return await operation()
            except self._sdk.rate_limit_error as exc:
                if attempt == 5:
                    raise ProviderError(
                        "E2B control plane rate limit exceeded",
                        code="provider_rate_limited",
                        retryable=True,
                        status_code=429,
                        headers={
                            "Retry-After": str(self.config.capacity_retry_after_seconds)
                        },
                    ) from exc
                provider_delay = self._provider_retry_after(exc)
                base_delay = (
                    provider_delay if provider_delay is not None else fallback_delay
                )
                jitter = random.uniform(0.0, min(max(base_delay * 0.2, 0.05), 2.0))
                delay = base_delay + jitter
                await self._sleep(delay)
                fallback_delay = min(fallback_delay * 2, 8)
        raise RuntimeError("unreachable")

    async def _list(self, metadata: dict[str, str]):
        try:
            async with self._list_lock:
                paginator = self._sdk.sandbox_cls.list(
                    query=self._sdk.query_cls(metadata=metadata),
                    limit=100,
                    **self._api_options(),
                )
                while paginator.has_next:
                    for item in await self._with_rate_limit_retry(paginator.next_items):
                        yield item
        except httpx.TransportError as exc:
            raise ProviderError(
                "E2B inventory is temporarily unavailable",
                code="provider_inventory_unavailable",
                retryable=True,
                status_code=503,
                headers={"Retry-After": "1"},
            ) from exc

    async def _find(
        self,
        sandbox_id: str,
        generation_token: str | None = None,
    ):
        cached = self._known_infos.get(sandbox_id)
        if cached is not None and (
            generation_token is None
            or cached.metadata.get("agentbox-generation") == generation_token
        ):
            return cached
        try:
            async for info in self._list(self._metadata(sandbox_id, generation_token)):
                normalized = self._normalize_info(info)
                metadata = normalized.metadata
                if metadata.get("agentbox-id") == sandbox_id and (
                    generation_token is None
                    or metadata.get("agentbox-generation") == generation_token
                ):
                    self._known_infos[sandbox_id] = normalized
                    return normalized
        except self._sdk.not_found_error:
            pass
        return None

    @staticmethod
    def _normalize_info(info) -> _E2BManagedInfo:  # type: ignore[no-untyped-def]
        state = getattr(info, "state", "running")
        return _E2BManagedInfo(
            provider_id=str(getattr(info, "sandbox_id", "")),
            metadata={
                str(key): str(value)
                for key, value in dict(getattr(info, "metadata", None) or {}).items()
            },
            state=str(getattr(state, "value", state)).lower(),
        )

    def invalidate_sandbox_cache(self, sandbox_id: str) -> None:
        self._sandboxes.pop(sandbox_id, None)
        self._known_infos.pop(sandbox_id, None)
        self._lease_renewed_at.pop(sandbox_id, None)

    def invalidate_endpoint_cache(self, sandbox_id: str) -> None:
        """Discard only the SDK connection, retaining the exact provider ID."""

        self._sandboxes.pop(sandbox_id, None)

    def _clear_lease_tracking(self, sandbox_id: str) -> None:
        self._lease_renewed_at.pop(sandbox_id, None)
        self._lease_renewal_locks.pop(sandbox_id, None)

    def _commit_complete_inventory(
        self,
        infos: list[_E2BManagedInfo],
        *,
        known_at_start: dict[str, _E2BManagedInfo],
        sandboxes_at_start: dict[str, object],
        preserve_missing: set[str],
    ) -> None:
        """Atomically prune stale scoped cache after a complete inventory.

        Cache entries created while inventory was in flight win over the older
        snapshot. A failed/partial inventory never reaches this commit point.
        """

        observed = {
            info.metadata["agentbox-id"]: info
            for info in infos
            if info.metadata.get("agentbox-id")
        }
        committed_infos = dict(observed)
        for sandbox_id in preserve_missing:
            info = self._known_infos.get(sandbox_id)
            if info is known_at_start.get(sandbox_id):
                committed_infos[sandbox_id] = info
        for sandbox_id, info in self._known_infos.items():
            if known_at_start.get(sandbox_id) is not info:
                committed_infos[sandbox_id] = info
        for sandbox_id in known_at_start:
            if sandbox_id not in self._known_infos:
                committed_infos.pop(sandbox_id, None)

        committed_sandboxes: dict[str, object] = {}
        for sandbox_id, sandbox in self._sandboxes.items():
            if sandboxes_at_start.get(sandbox_id) is not sandbox:
                committed_sandboxes[sandbox_id] = sandbox
                continue
            info = committed_infos.get(sandbox_id)
            if (
                info is not None
                and str(getattr(sandbox, "sandbox_id", "")) == info.provider_id
            ):
                committed_sandboxes[sandbox_id] = sandbox

        # No awaits occur between these assignments, so other coroutines cannot
        # observe a partially pruned cache pair.
        self._known_infos = committed_infos
        self._sandboxes = committed_sandboxes

    async def _preserve_missing_inventory_entries(
        self,
        infos: list[_E2BManagedInfo],
        *,
        known_at_start: dict[str, _E2BManagedInfo],
        sandboxes_at_start: dict[str, object],
    ) -> set[str]:
        """Never treat an empty eventual inventory as exact deletion proof."""

        del sandboxes_at_start
        observed_ids = {info.metadata.get("agentbox-id") for info in infos}
        return {
            sandbox_id
            for sandbox_id in known_at_start
            if sandbox_id not in observed_ids
        }

    async def _connect(
        self,
        sandbox_id: str,
        info: _E2BManagedInfo | None = None,
        *,
        resume: bool = False,
    ):
        cached = self._sandboxes.get(sandbox_id)
        if cached is not None:
            if (
                info is None
                or str(getattr(cached, "sandbox_id", "")) == info.provider_id
            ):
                return cached
            self._sandboxes.pop(sandbox_id, None)
        info = info or await self._find(sandbox_id)
        if info is None:
            return None
        if info.state != "running" and not resume:
            raise ProviderError(
                f"E2B sandbox {sandbox_id} is suspended",
                code="sandbox_suspended",
                status_code=409,
            )
        # Once an exact provider ID is known, a connect 404 is ambiguous under
        # E2B's eventually consistent control plane. Retry only that exact ID;
        # never turn the miss into logical absence or replacement permission.
        resume_deadline = self._clock() + self.config.status_retry_seconds
        while True:
            try:
                sandbox = await self._with_rate_limit_retry(
                    lambda: self._sdk.sandbox_cls.connect(
                        info.provider_id,
                        timeout=self.config.timeout_seconds,
                        **self._api_options(),
                    )
                )
                break
            except (
                self._sdk.not_found_error,
                self._sdk.sandbox_error,
                httpx.TransportError,
            ):
                # A transient control-plane 404 does not erase the exact
                # durable provider identity or permit a replacement
                # generation. Resume retries only this exact provider ID.
                self.invalidate_endpoint_cache(sandbox_id)
                if self._clock() >= resume_deadline:
                    raise ProviderError(
                        "E2B durable provider generation could not be reconnected",
                        code="provider_adoption_unavailable",
                        retryable=True,
                        status_code=503,
                        headers={"Retry-After": "1"},
                    )
                await self._sleep(random.uniform(0.05, 0.2))
        self._sandboxes[sandbox_id] = sandbox
        self._known_infos[sandbox_id] = _E2BManagedInfo(
            provider_id=info.provider_id,
            metadata=info.metadata,
            state="running",
        )
        self._lease_renewed_at[sandbox_id] = self._clock()
        await self._record_capacity_observed(
            sandbox_id, getattr(sandbox, "sandbox_id", None)
        )
        return sandbox

    async def adopt(self, sandbox_id: str, provider_id: str) -> bool:
        """Reconnect the durable E2B generation without relying on list()."""

        cached = self._sandboxes.get(sandbox_id)
        cached_provider_id = (
            str(getattr(cached, "sandbox_id", "")) if cached is not None else None
        )
        known = self._known_infos.get(sandbox_id)
        known_provider_id = known.provider_id if known is not None else None
        if any(
            observed_id and observed_id != provider_id
            for observed_id in (cached_provider_id, known_provider_id)
        ):
            # Process-local inventory/cache is advisory. The provider ID in
            # the durable state store is authoritative.
            self.invalidate_sandbox_cache(sandbox_id)
            cached = None
            known = None
        if cached is not None:
            return True

        info = _E2BManagedInfo(
            provider_id=provider_id,
            metadata=known.metadata
            if known is not None
            else self._metadata(sandbox_id),
            state="running",
        )
        deadline = self._clock() + self.config.status_retry_seconds
        last_error: Exception | None = None
        sandbox = None
        while True:
            try:
                sandbox = await self._with_rate_limit_retry(
                    lambda: self._sdk.sandbox_cls.connect(
                        provider_id,
                        timeout=self.config.timeout_seconds,
                        **self._api_options(),
                    )
                )
                break
            except (
                self._sdk.not_found_error,
                self._sdk.sandbox_error,
                httpx.TransportError,
            ) as exc:
                # A provider 404 is not authoritative absence: real E2B routes
                # and connect calls can temporarily disagree while the exact
                # generation remains alive. Explicit DELETE is the only path
                # that clears durable identity and permits replacement.
                last_error = exc
                if self._clock() >= deadline:
                    raise ProviderError(
                        "E2B durable provider generation could not be reconnected",
                        code="provider_adoption_unavailable",
                        retryable=True,
                        status_code=503,
                        headers={"Retry-After": "1"},
                    ) from last_error
                await self._sleep(random.uniform(0.05, 0.2))
        assert sandbox is not None
        if str(getattr(sandbox, "sandbox_id", "")) != provider_id:
            self.invalidate_sandbox_cache(sandbox_id)
            raise ProviderError(
                "E2B adoption returned a different provider generation",
                code="endpoint_generation_changed",
                retryable=True,
                status_code=409,
            )
        self._sandboxes[sandbox_id] = sandbox
        self._known_infos[sandbox_id] = info
        self._lease_renewed_at[sandbox_id] = self._clock()
        await self._record_capacity_observed(sandbox_id, provider_id)
        return True

    async def _active_provider_ids(self) -> set[str]:
        provider_ids: set[str] = set()
        async for info in self._list(self._metadata()):
            normalized = self._normalize_info(info)
            if normalized.state == "running":
                provider_ids.add(normalized.provider_id)
        return provider_ids

    async def _initialize_capacity(self) -> None:
        if self._observed_active_count is not None:
            return
        async with self._capacity_init_lock:
            while self._observed_active_count is None:
                async with self._capacity_condition:
                    generation = self._capacity_generation
                provider_ids = await self._active_provider_ids()
                async with self._capacity_condition:
                    if self._observed_active_count is not None:
                        return
                    if generation != self._capacity_generation:
                        continue
                    self._counted_provider_ids = provider_ids
                    self._observed_active_count = len(provider_ids)
                    self._capacity_generation += 1
                    self._capacity_condition.notify_all()

    async def _reserve_capacity(self, sandbox_id: str) -> None:
        await self._initialize_capacity()
        deadline = self._clock() + self.config.admission_wait_seconds
        async with self._capacity_condition:
            while (self._observed_active_count or 0) + len(
                self._capacity_reservations
            ) >= self.config.max_active:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    logger.warning(
                        "agentbox.e2b.capacity_exhausted",
                        sandbox_id=sandbox_id,
                        observed_active_count=self._observed_active_count or 0,
                        capacity_limit=self.config.max_active,
                    )
                    raise ProviderError(
                        f"E2B concurrency limit ({self.config.max_active}) reached",
                        code="capacity_exhausted",
                        retryable=True,
                        status_code=429,
                        headers={
                            "Retry-After": str(self.config.capacity_retry_after_seconds)
                        },
                    )
                try:
                    await asyncio.wait_for(
                        self._capacity_condition.wait(),
                        timeout=remaining,
                    )
                except TimeoutError:
                    continue
            self._capacity_reservations.add(sandbox_id)
            self._capacity_generation += 1

    async def _finish_reservation(
        self,
        sandbox_id: str,
        *,
        created: bool,
        provider_id: str | None = None,
    ) -> None:
        async with self._capacity_condition:
            if sandbox_id not in self._capacity_reservations:
                return
            self._capacity_reservations.remove(sandbox_id)
            self._ambiguous_capacity_reservations.discard(sandbox_id)
            if created:
                if not provider_id:
                    raise RuntimeError("created E2B reservation is missing provider ID")
                self._counted_provider_ids.add(provider_id)
                self._observed_active_count = len(self._counted_provider_ids)
            self._capacity_generation += 1
            self._capacity_condition.notify_all()

    async def _mark_reservation_ambiguous(self, sandbox_id: str) -> None:
        """Retain admission until a successful inventory resolves uncertainty."""

        async with self._capacity_condition:
            if sandbox_id in self._capacity_reservations:
                self._ambiguous_capacity_reservations.add(sandbox_id)
                self._capacity_generation += 1

    async def _record_capacity_removed(
        self, sandbox_id: str, provider_id: str | None
    ) -> None:
        del sandbox_id
        async with self._capacity_condition:
            if provider_id:
                self._counted_provider_ids.discard(provider_id)
            if self._observed_active_count is not None:
                self._observed_active_count = len(self._counted_provider_ids)
            self._capacity_generation += 1
            self._capacity_condition.notify_all()

    async def _record_capacity_observed(
        self, sandbox_id: str, provider_id: str | None
    ) -> None:
        if not provider_id:
            return
        async with self._capacity_condition:
            if sandbox_id in self._capacity_reservations:
                return
            if self._observed_active_count is None:
                return
            if provider_id in self._counted_provider_ids:
                return
            self._counted_provider_ids.add(provider_id)
            self._observed_active_count = len(self._counted_provider_ids)
            self._capacity_generation += 1
            self._capacity_condition.notify_all()

    async def _refresh_capacity_inventory(self) -> None:
        known_at_start = dict(self._known_infos)
        sandboxes_at_start = dict(self._sandboxes)
        async with self._capacity_condition:
            inventory_generation = self._capacity_generation
        normalized_infos = [
            self._normalize_info(info) async for info in self._list(self._metadata())
        ]
        provider_ids = {
            info.provider_id for info in normalized_infos if info.state == "running"
        }
        preserve_missing = await self._preserve_missing_inventory_entries(
            normalized_infos,
            known_at_start=known_at_start,
            sandboxes_at_start=sandboxes_at_start,
        )
        provider_ids.update(
            known_at_start[sandbox_id].provider_id
            for sandbox_id in preserve_missing
            if known_at_start[sandbox_id].state == "running"
        )
        self._commit_complete_inventory(
            normalized_infos,
            known_at_start=known_at_start,
            sandboxes_at_start=sandboxes_at_start,
            preserve_missing=preserve_missing,
        )
        async with self._capacity_condition:
            if (
                inventory_generation == self._capacity_generation
                and not self._capacity_reservations
            ):
                self._counted_provider_ids = provider_ids
                self._observed_active_count = len(provider_ids)
                self._capacity_generation += 1
            self._capacity_condition.notify_all()

    async def _wait_for_create_rate_slot(self) -> None:
        interval = 1.0 / self.config.create_rate_per_second
        async with self._create_rate_lock:
            now = self._clock()
            slot = max(now, self._next_create_at)
            self._next_create_at = slot + interval
        delay = slot - now
        if delay > 0:
            await self._sleep(delay)

    async def _create_provider_sandbox(self, operation, sandbox_id: str):  # type: ignore[no-untyped-def]
        async with self._create_semaphore:
            async with self._capacity_condition:
                self._create_in_flight += 1
            try:

                async def rate_limited_operation():
                    await self._wait_for_create_rate_slot()
                    return await operation()

                return await self._with_rate_limit_retry(rate_limited_operation)
            finally:
                async with self._capacity_condition:
                    self._create_in_flight -= 1
                    self._capacity_condition.notify_all()

    async def bootstrap(
        self,
        sandbox_id: str,
        request: SandboxEnsureRequest,
    ) -> None:
        sandbox = await self._connect(sandbox_id, resume=True)
        if sandbox is None:
            raise SandboxNotFoundError(sandbox_id)
        sandbox = await self._bootstrap_sandbox(sandbox_id, sandbox, request.env)
        self._sandboxes[sandbox_id] = sandbox

    async def _bootstrap_sandbox(
        self,
        sandbox_id: str,
        sandbox,
        env: dict[str, str],
    ):
        """Start the runtime after create-time env exists in the sandbox.

        E2B template start commands run during the build snapshot and cannot
        see Sandbox.create(envs=...). The provider hook is intentionally narrow:
        it starts the common runtime entrypoint but never implements runtime
        sessions, function execution, or app transport.
        """

        deadline = self._clock() + self.config.runtime_bootstrap_timeout_seconds
        del sandbox_id
        runtime_started = False
        health_command = (
            'python -c "import urllib.request; '
            "[urllib.request.urlopen('http://127.0.0.1:%d/health' % port, "
            'timeout=1).read() for port in (8080, 8090)]"'
        )
        last_error: Exception | None = None
        while self._clock() < deadline:
            try:
                # A newly created E2B sandbox can be visible to the control
                # plane before its commands data plane accepts connections.
                # Limit retries to bootstrap: replaying arbitrary user
                # commands would not be safe. If starting the runtime had an
                # ambiguous outcome, re-listing processes on the next pass
                # prevents a duplicate start.
                if not runtime_started:
                    remaining = max(min(deadline - self._clock(), 10.0), 0.1)
                    processes = await asyncio.wait_for(
                        self._with_rate_limit_retry(sandbox.commands.list),
                        timeout=remaining,
                    )
                    running = any(
                        "/usr/local/bin/start-runtime"
                        in " ".join(
                            [
                                str(getattr(process, "cmd", "")),
                                *map(str, getattr(process, "args", [])),
                            ]
                        )
                        for process in processes
                    )
                    if not running:
                        await asyncio.wait_for(
                            self._with_rate_limit_retry(
                                lambda: sandbox.commands.run(
                                    "/usr/local/bin/start-runtime",
                                    background=True,
                                    envs={
                                        **env,
                                        "PYTHONPATH": self.config.runtime_pythonpath,
                                    },
                                    user=self.config.runtime_user,
                                    timeout=self.config.timeout_seconds,
                                )
                            ),
                            timeout=remaining,
                        )
                    runtime_started = True

                # The provider object being running is not enough: E2B can
                # accept the background command before the imported OCI
                # runtime and function executor are listening. Require both
                # local and authenticated public health before publishing
                # readiness.
                remaining = max(min(deadline - self._clock(), 5.0), 0.1)
                result = await asyncio.wait_for(
                    self._with_rate_limit_retry(
                        lambda: sandbox.commands.run(
                            health_command,
                            envs={"PYTHONPATH": self.config.runtime_pythonpath},
                            user=self.config.runtime_user,
                            timeout=3,
                        )
                    ),
                    timeout=remaining,
                )
                if getattr(result, "exit_code", 1) != 0:
                    await self._sleep(0.25)
                    continue

                if await self._public_runtime_ready(sandbox):
                    return sandbox
            except (
                self._sdk.not_found_error,
                self._sdk.sandbox_error,
                httpx.TransportError,
                TimeoutError,
            ) as exc:
                # E2B can accept create before its command transport is ready.
                # Retry only operations on this already-fenced SDK object.
                # Bootstrap never reconnects, inventories, or creates another
                # sandbox generation.
                last_error = exc
            await self._sleep(0.25)
        raise ProviderError(
            "E2B runtime did not become ready inside the sandbox",
            code="runtime_bootstrap_failed",
            retryable=True,
            status_code=504,
        ) from last_error

    async def _public_runtime_ready(
        self,
        sandbox,  # type: ignore[no-untyped-def]
        *,
        request_timeout: float | None = None,
    ) -> bool:
        """Use E2B's authenticated runtime gateway as data-plane evidence."""

        token = getattr(sandbox, "traffic_access_token", None)
        if not token:
            return False

        endpoint_timeout = 5.0
        if request_timeout is not None:
            endpoint_timeout = max(min(endpoint_timeout, request_timeout), 0.001)

        def check_public_endpoint() -> bool:
            for port in (8080, 8090):
                public_request = urlrequest.Request(
                    f"https://{sandbox.get_host(port)}/health",
                    headers={"e2b-traffic-access-token": str(token)},
                    method="GET",
                )
                try:
                    with self._sdk.urlopen(
                        public_request, timeout=endpoint_timeout
                    ) as response:
                        if not 200 <= response.status < 300:
                            return False
                except (urlerror.URLError, OSError, TimeoutError):
                    return False
            return True

        return await asyncio.to_thread(check_public_endpoint)

    async def _bounded_status_probe(
        self,
        operation: Callable[[float], Awaitable[Any]],
        *,
        deadline: float,
    ) -> Any:
        """Run one status probe without exceeding the shared status deadline."""

        remaining = min(
            self.config.request_timeout_seconds,
            max(deadline - self._clock(), 0.0),
        )
        if remaining <= 0:
            raise TimeoutError("E2B sandbox status probe deadline expired")
        return await asyncio.wait_for(operation(remaining), timeout=remaining)

    async def _renew_timeout(self, sandbox, sandbox_id: str) -> bool:  # type: ignore[no-untyped-def]
        """Best-effort lease renewal without rejecting healthy data-plane work."""

        deadline = self._clock() + self.config.status_retry_seconds
        last_error: Exception | None = None
        while True:
            try:
                await self._with_rate_limit_retry(
                    lambda: sandbox.set_timeout(
                        self.config.timeout_seconds, **self._api_options()
                    )
                )
                return True
            except (
                self._sdk.not_found_error,
                self._sdk.sandbox_error,
                httpx.TransportError,
            ) as exc:
                last_error = exc
                if await self._public_runtime_ready(sandbox):
                    logger.debug(
                        "agentbox.e2b.timeout_renewal_deferred.diagnostic",
                        sandbox_id=sandbox_id,
                    )
                    return False
                if self._clock() >= deadline:
                    raise ProviderError(
                        "E2B timeout renewal remained unavailable",
                        code="provider_lease_unavailable",
                        retryable=True,
                        status_code=503,
                    ) from last_error
                await self._sleep(random.uniform(0.05, 0.2))

    async def renew_lease(
        self,
        sandbox_id: str,
        *,
        provider_id: str | None = None,
    ) -> None:
        """Extend one running sandbox timeout at most once per timeout third."""

        lock = self._lease_renewal_locks.setdefault(sandbox_id, asyncio.Lock())
        async with lock:
            now = self._clock()
            renewed_at = self._lease_renewed_at.get(sandbox_id)
            if (
                renewed_at is not None
                and now - renewed_at < self.config.timeout_seconds / 3
            ):
                return
            sandbox = self._sandboxes.get(sandbox_id)
            if (
                sandbox is not None
                and provider_id is not None
                and str(getattr(sandbox, "sandbox_id", "")) != provider_id
            ):
                self.invalidate_endpoint_cache(sandbox_id)
                sandbox = None
            if sandbox is None:
                info = (
                    _E2BManagedInfo(
                        provider_id=provider_id,
                        metadata=self._metadata(sandbox_id),
                        state="running",
                    )
                    if provider_id is not None
                    else None
                )
                sandbox = await self._connect(sandbox_id, info)
                if sandbox is None:
                    raise SandboxNotFoundError(sandbox_id)
            if await self._renew_timeout(sandbox, sandbox_id):
                self._lease_renewed_at[sandbox_id] = self._clock()

    async def create(
        self, sandbox_id: str, request: SandboxEnsureRequest
    ) -> SandboxInternalStatus:
        return await self._create(sandbox_id, request, generation_token=None)

    async def create_generation(
        self,
        sandbox_id: str,
        request: SandboxEnsureRequest,
        *,
        generation_token: str,
    ) -> SandboxInternalStatus:
        return await self._create(
            sandbox_id,
            request,
            generation_token=generation_token,
        )

    async def _create(
        self,
        sandbox_id: str,
        request: SandboxEnsureRequest,
        *,
        generation_token: str | None,
    ) -> SandboxInternalStatus:
        lock = self._create_locks.setdefault(sandbox_id, asyncio.Lock())
        async with lock:
            existing = await self._find(sandbox_id, generation_token)
            if existing is not None:
                if existing.state == "running":
                    try:
                        sandbox = await self._connect(sandbox_id, existing)
                        if sandbox is not None:
                            await self.renew_lease(sandbox_id)
                            return await self._status(sandbox_id, sandbox)
                        raise SandboxNotFoundError(sandbox_id)
                    except SandboxNotFoundError as exc:
                        self.invalidate_endpoint_cache(sandbox_id)
                        raise ProviderError(
                            "E2B durable provider generation could not be reconnected",
                            code="provider_adoption_unavailable",
                            retryable=True,
                            status_code=503,
                            headers={"Retry-After": "1"},
                        ) from exc
                else:
                    await self._reserve_capacity(sandbox_id)
                    resumed = False
                    try:
                        sandbox = await self._connect(sandbox_id, existing, resume=True)
                        if sandbox is None:
                            raise SandboxNotFoundError(sandbox_id)
                        await self._finish_reservation(
                            sandbox_id,
                            created=True,
                            provider_id=existing.provider_id,
                        )
                        resumed = True
                        return await self._status(sandbox_id, sandbox)
                    except SandboxNotFoundError as exc:
                        self.invalidate_endpoint_cache(sandbox_id)
                        raise ProviderError(
                            "E2B durable provider generation could not be resumed",
                            code="provider_adoption_unavailable",
                            retryable=True,
                            status_code=503,
                            headers={"Retry-After": "1"},
                        ) from exc
                    except Exception:
                        if resumed:
                            await self.release(sandbox_id)
                        raise
                    finally:
                        if not resumed:
                            await self._finish_reservation(sandbox_id, created=False)

            await self._reserve_capacity(sandbox_id)
            reservation_finished = False
            sandbox = None
            try:
                try:

                    async def create_at_provider():
                        return await self._sdk.sandbox_cls.create(
                            self.config.template,
                            timeout=self.config.timeout_seconds,
                            metadata=self._metadata(sandbox_id, generation_token),
                            envs=request.env,
                            secure=True,
                            allow_internet_access=self.config.allow_internet_access,
                            # Use E2B's native authenticated public gateway.
                            # Egress remains provider-default; no CIDR policy is
                            # imposed on E2B sandboxes.
                            network={"allow_public_traffic": False},
                            lifecycle={
                                "on_timeout": {
                                    "action": "pause",
                                    "keep_memory": False,
                                },
                                "auto_resume": False,
                            },
                            **self._api_options(),
                        )

                    sandbox = await self._create_provider_sandbox(
                        create_at_provider, sandbox_id
                    )
                except self._sdk.definitive_create_errors as exc:
                    raise ProviderError(
                        "E2B rejected sandbox creation before acceptance",
                        code="provider_create_rejected",
                        retryable=False,
                        status_code=400,
                    ) from exc
                except self._sdk.sandbox_error as exc:
                    # A transport timeout can happen after E2B accepted create.
                    # Re-list only this durable create attempt before freeing
                    # capacity. A visible older generation for the same user
                    # must never be mistaken for this accepted request.
                    try:
                        info = await self._find(sandbox_id, generation_token)
                    except Exception as lookup_exc:
                        # Keep the local reservation counted until a later
                        # inventory pass can prove whether create succeeded.
                        await self._mark_reservation_ambiguous(sandbox_id)
                        reservation_finished = True
                        raise ProviderError(
                            "E2B create outcome is unknown",
                            code="provider_create_outcome_unknown",
                            retryable=True,
                        ) from lookup_exc
                    if info is None:
                        await self._mark_reservation_ambiguous(sandbox_id)
                        reservation_finished = True
                        raise ProviderError(
                            "E2B create outcome is unknown",
                            code="provider_create_outcome_unknown",
                            retryable=True,
                        ) from exc
                    sandbox = await self._connect(
                        sandbox_id,
                        info,
                        resume=info.state != "running",
                    )
                    if sandbox is None:
                        await self._mark_reservation_ambiguous(sandbox_id)
                        reservation_finished = True
                        raise ProviderError(
                            "E2B create outcome is unknown",
                            code="provider_create_outcome_unknown",
                            retryable=True,
                        ) from exc
                self._sandboxes[sandbox_id] = sandbox
                self._lease_renewed_at[sandbox_id] = self._clock()
                provider_id = getattr(sandbox, "sandbox_id", None)
                if provider_id:
                    self._known_infos[sandbox_id] = _E2BManagedInfo(
                        provider_id=str(provider_id),
                        metadata=self._metadata(sandbox_id, generation_token),
                        state="running",
                    )
                await self._finish_reservation(
                    sandbox_id,
                    created=True,
                    provider_id=provider_id,
                )
                reservation_finished = True
                try:
                    return await self._status(sandbox_id, sandbox)
                except Exception as exc:
                    # The provider already accepted create and returned an
                    # exact ID. A post-create status failure is ambiguous: do
                    # not delete the only generation and strand the durable
                    # attempt token. Recovery will adopt this exact token/ID.
                    raise ProviderError(
                        "E2B create succeeded but initial status is unavailable",
                        code="provider_create_outcome_unknown",
                        retryable=True,
                        status_code=503,
                        headers={"Retry-After": "1"},
                    ) from exc
            finally:
                if not reservation_finished:
                    await self._finish_reservation(sandbox_id, created=False)

    async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
        sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is not None:
            status = await self._status(sandbox_id, sandbox)
            if status.ready:
                return status
            provider_id = str(getattr(sandbox, "sandbox_id", ""))
            known = self._known_infos.get(sandbox_id)
            if known is not None and known.provider_id == provider_id:
                self._known_infos[sandbox_id] = _E2BManagedInfo(
                    provider_id=known.provider_id,
                    metadata=known.metadata,
                    state="paused",
                )
            self.invalidate_endpoint_cache(sandbox_id)
            self._clear_lease_tracking(sandbox_id)
            if provider_id:
                await self._record_capacity_removed(sandbox_id, provider_id)
            return status
        info = await self._find(sandbox_id)
        if info is None:
            raise SandboxNotFoundError(sandbox_id)
        return self._status_from_info(sandbox_id, info)

    @staticmethod
    def _status_from_info(
        sandbox_id: str, info: _E2BManagedInfo
    ) -> SandboxInternalStatus:
        ready = info.state == "running"
        return SandboxInternalStatus(
            id=sandbox_id,
            ready=ready,
            status="RUNNING" if ready else "STOPPED",
            apps={
                app.name: SandboxInternalAppStatus(
                    name=app.name,
                    public_slug=app.public_slug,
                    port=app.port,
                    ready=ready,
                )
                for app in SANDBOX_APPS.values()
            },
        )

    async def _status(self, sandbox_id: str, sandbox) -> SandboxInternalStatus:
        started_at = self._clock()
        # Status is a hot read on every session/function handout. Never let the
        # provider's general 15-20 second retry budgets leak into this path.
        # Data-plane health, control-plane status, and an optional exact-ID
        # reconnect share one strict two-second observation budget.
        probe_deadline = started_at + min(
            self.config.request_timeout_seconds,
            2.0,
        )
        current = sandbox
        last_error: Exception | None = None

        async def observe(candidate) -> tuple[bool | None, Exception | None]:  # type: ignore[no-untyped-def]
            try:
                public_ready = bool(
                    await self._bounded_status_probe(
                        lambda remaining: self._public_runtime_ready(
                            candidate,
                            # Two public ports are checked serially. Give each
                            # at most 500ms so a slow gateway still leaves time
                            # for the authoritative control-plane check.
                            request_timeout=min(remaining / 2, 0.5),
                        ),
                        deadline=probe_deadline,
                    )
                )
            except TimeoutError:
                public_ready = False
            if public_ready:
                return True, None

            try:
                return (
                    bool(
                        await self._bounded_status_probe(
                            lambda _remaining: candidate.is_running(),
                            deadline=probe_deadline,
                        )
                    ),
                    None,
                )
            except (
                self._sdk.not_found_error,
                self._sdk.sandbox_error,
                httpx.TransportError,
                TimeoutError,
            ) as exc:
                return None, exc

        running, last_error = await observe(current)
        if running is not True:
            info = self._known_infos.get(sandbox_id)
            if info is not None and info.state == "running":
                try:
                    reconnected = await self._bounded_status_probe(
                        lambda _remaining: self._with_rate_limit_retry(
                            lambda: self._sdk.sandbox_cls.connect(
                                info.provider_id,
                                timeout=self.config.timeout_seconds,
                                **self._api_options(),
                            )
                        ),
                        deadline=probe_deadline,
                    )
                except (
                    self._sdk.not_found_error,
                    self._sdk.sandbox_error,
                    httpx.TransportError,
                    TimeoutError,
                ) as exc:
                    # A successful control-plane `false` is authoritative
                    # enough to report STOPPED. An unavailable first check is
                    # not: preserve the durable identity and fail closed.
                    if running is None:
                        last_error = exc
                    else:
                        running = False
                else:
                    if str(getattr(reconnected, "sandbox_id", "")) != info.provider_id:
                        last_error = ProviderError(
                            "E2B reconnect returned a different provider generation",
                            code="endpoint_generation_changed",
                            retryable=True,
                            status_code=409,
                        )
                        running = None
                    else:
                        current = reconnected
                        self._sandboxes[sandbox_id] = current
                        logger.debug('agentbox.e2b.e2b_status_reconnected_sandbox_id.observed', sandbox_id=sandbox_id, provider_id=info.provider_id)
                        running, last_error = await observe(current)

            if running is None:
                self.invalidate_endpoint_cache(sandbox_id)
                raise ProviderError(
                    "E2B exact sandbox status is temporarily unavailable",
                    code="provider_adoption_unavailable",
                    retryable=True,
                    status_code=503,
                    headers={"Retry-After": "1"},
                ) from last_error

        assert running is not None
        sandbox = current
        apps = {
            app.name: SandboxInternalAppStatus(
                name=app.name,
                public_slug=app.public_slug,
                port=app.port,
                ready=running,
                private_url=(
                    f"https://{sandbox.get_host(app.port)}" if running else None
                ),
            )
            for app in SANDBOX_APPS.values()
        }
        runtime_url = apps["runtime"].private_url
        return SandboxInternalStatus(
            id=sandbox_id,
            ready=running,
            status="RUNNING" if running else "STOPPED",
            runtime_url=runtime_url,
            apps=apps,
        )

    async def list_managed(self) -> list[ManagedSandbox]:
        known_at_start = dict(self._known_infos)
        sandboxes_at_start = dict(self._sandboxes)
        async with self._capacity_condition:
            ambiguous_at_start = tuple(self._ambiguous_capacity_reservations)
        managed: list[ManagedSandbox] = []
        infos: list[_E2BManagedInfo] = []
        async for raw_info in self._list(self._metadata()):
            info = self._normalize_info(raw_info)
            infos.append(info)
            metadata = info.metadata
            sandbox_id = metadata.get("agentbox-id")
            if not sandbox_id:
                continue
            managed.append(
                ManagedSandbox(
                    ref=SandboxRef(sandbox_id, info.provider_id),
                    status=self._status_from_info(sandbox_id, info),
                    instance_id=info.provider_id,
                    metadata=metadata,
                )
            )
        preserve_missing = await self._preserve_missing_inventory_entries(
            infos,
            known_at_start=known_at_start,
            sandboxes_at_start=sandboxes_at_start,
        )
        for sandbox_id in preserve_missing:
            info = known_at_start[sandbox_id]
            managed.append(
                ManagedSandbox(
                    ref=SandboxRef(sandbox_id, info.provider_id),
                    status=self._status_from_info(sandbox_id, info),
                    instance_id=info.provider_id,
                    metadata=info.metadata,
                )
            )
        self._commit_complete_inventory(
            infos,
            known_at_start=known_at_start,
            sandboxes_at_start=sandboxes_at_start,
            preserve_missing=preserve_missing,
        )
        by_sandbox = {
            item.ref.sandbox_id: item.ref.provider_id
            for item in managed
            if item.status.status in {"CREATING", "RUNNING"}
        }
        for sandbox_id in ambiguous_at_start:
            provider_id = by_sandbox.get(sandbox_id)
            await self._finish_reservation(
                sandbox_id,
                created=provider_id is not None,
                provider_id=provider_id,
            )
        return managed

    async def release(self, sandbox_id: str) -> bool:
        info = await self._find(sandbox_id)
        if info is None:
            self._clear_lease_tracking(sandbox_id)
            await self._refresh_capacity_inventory()
            return False
        if info.state != "running":
            self._clear_lease_tracking(sandbox_id)
            await self._record_capacity_removed(sandbox_id, info.provider_id)
            return False
        try:
            released = bool(
                await self._with_rate_limit_retry(
                    lambda: self._sdk.sandbox_cls.pause(
                        info.provider_id,
                        keep_memory=False,
                        **self._api_options(),
                    )
                )
            )
        except self._sdk.not_found_error:
            self.invalidate_sandbox_cache(sandbox_id)
            self._clear_lease_tracking(sandbox_id)
            await self._refresh_capacity_inventory()
            return False
        except self._sdk.sandbox_error as exc:
            raise ProviderError(
                f"E2B sandbox release failed: {exc}", retryable=True
            ) from exc
        self._sandboxes.pop(sandbox_id, None)
        self._clear_lease_tracking(sandbox_id)
        self._known_infos[sandbox_id] = _E2BManagedInfo(
            provider_id=info.provider_id,
            metadata=info.metadata,
            state="paused",
        )
        await self._record_capacity_removed(sandbox_id, info.provider_id)
        return released

    async def delete(self, sandbox_id: str) -> bool:
        info = await self._find(sandbox_id)
        self._sandboxes.pop(sandbox_id, None)
        if info is None:
            self._clear_lease_tracking(sandbox_id)
            await self._refresh_capacity_inventory()
            return False
        try:
            logger.debug('agentbox.e2b.e2b_kill_requested_reason_logical.diagnostic', sandbox_id=sandbox_id, provider_id=info.provider_id)
            deleted = bool(
                await self._with_rate_limit_retry(
                    lambda: self._sdk.sandbox_cls.kill(
                        info.provider_id, **self._api_options()
                    )
                )
            )
            if not deleted:
                logger.debug('agentbox.e2b.e2b_kill_already_absent_reason.observed', sandbox_id=sandbox_id, provider_id=info.provider_id)
            await self._record_capacity_removed(sandbox_id, info.provider_id)
            self._known_infos.pop(sandbox_id, None)
            self._clear_lease_tracking(sandbox_id)
            # E2B returns False only when this exact provider-generated ID no
            # longer exists. The requested terminal state is already reached.
            return True
        except self._sdk.not_found_error:
            logger.debug('agentbox.e2b.e2b_kill_already_absent_reason.observed', sandbox_id=sandbox_id, provider_id=info.provider_id)
            await self._record_capacity_removed(sandbox_id, info.provider_id)
            self._known_infos.pop(sandbox_id, None)
            self._clear_lease_tracking(sandbox_id)
            return True
        except self._sdk.sandbox_error as exc:
            raise ProviderError(
                f"E2B sandbox deletion failed: {exc}", retryable=True
            ) from exc

    async def _discard_provider(
        self, sandbox_id: str, provider_id: str | None = None
    ) -> None:
        cached = self._sandboxes.pop(sandbox_id, None)
        self._known_infos.pop(sandbox_id, None)
        self._clear_lease_tracking(sandbox_id)
        provider_id = provider_id or getattr(cached, "sandbox_id", None)
        if not provider_id:
            await self._refresh_capacity_inventory()
            raise ProviderError(
                "E2B cleanup outcome is unknown",
                code="provider_cleanup_outcome_unknown",
                retryable=True,
            )
        try:
            logger.debug('agentbox.e2b.e2b_kill_requested_reason_create.diagnostic', sandbox_id=sandbox_id, provider_id=provider_id)
            await self._with_rate_limit_retry(
                lambda: self._sdk.sandbox_cls.kill(provider_id, **self._api_options())
            )
        except self._sdk.not_found_error:
            pass
        except Exception as exc:
            logger.debug('agentbox.e2b.discard_unbootstrapped_e2b_sandbox_s.propagated', sandbox_id=sandbox_id, exc_info=True)
            raise ProviderError(
                "E2B cleanup outcome is unknown",
                code="provider_cleanup_outcome_unknown",
                retryable=True,
            ) from exc
        await self._record_capacity_removed(sandbox_id, provider_id)

    async def purge_managed(self, ref: SandboxRef) -> bool:
        """Purge one exact provider generation without targeting a replacement."""

        try:
            logger.debug('agentbox.e2b.e2b_kill_requested_reason_orphan.diagnostic', sandbox_id=ref.sandbox_id, provider_id=ref.provider_id)
            deleted = bool(
                await self._with_rate_limit_retry(
                    lambda: self._sdk.sandbox_cls.kill(
                        ref.provider_id, **self._api_options()
                    )
                )
            )
            if not deleted:
                logger.debug('agentbox.e2b.e2b_kill_already_absent_reason.observed', sandbox_id=ref.sandbox_id, provider_id=ref.provider_id)
        except self._sdk.not_found_error:
            logger.debug('agentbox.e2b.e2b_kill_already_absent_reason.observed', sandbox_id=ref.sandbox_id, provider_id=ref.provider_id)
            deleted = False
        except self._sdk.sandbox_error as exc:
            raise ProviderError(
                f"E2B managed sandbox purge failed: {exc}", retryable=True
            ) from exc
        known = self._known_infos.get(ref.sandbox_id)
        if known is None or known.provider_id == ref.provider_id:
            self.invalidate_sandbox_cache(ref.sandbox_id)
        await self._record_capacity_removed(ref.sandbox_id, ref.provider_id)
        # E2B returns False for a 404 from DELETE /sandboxes/{sandboxID}.
        # Unlike an eventually-consistent list/connect miss, this exact-ID
        # deletion response proves the requested terminal state is reached.
        return True

    async def resolve_endpoint(
        self,
        sandbox_id: str,
        app: SandboxAppSpec,
        *,
        protocol: EndpointProtocol = "http",
    ) -> SandboxEndpoint:
        del protocol
        sandbox = await self._connect(sandbox_id)
        if sandbox is None:
            raise SandboxNotFoundError(sandbox_id)
        return self._endpoint_from_sandbox(sandbox_id, sandbox, app)

    async def refresh_endpoint(
        self,
        sandbox_id: str,
        app: SandboxAppSpec,
        *,
        instance_id: str,
        protocol: EndpointProtocol = "http",
    ) -> SandboxEndpoint:
        """Refresh one proven provider route without consulting inventory.

        E2B list results are eventually consistent immediately after create.
        The structured gateway miss already supplies the exact provider ID, so
        reconnect that generation directly to refresh its domain and traffic
        token. Never delete or pause compute on refresh failure.
        """

        del protocol
        known = self._known_infos.get(sandbox_id)
        if known is not None and known.provider_id != instance_id:
            raise ProviderError(
                "E2B endpoint generation changed during route refresh",
                code="endpoint_generation_changed",
                retryable=True,
                status_code=409,
            )
        cached = self._sandboxes.get(sandbox_id)
        cached_provider_id = (
            str(getattr(cached, "sandbox_id", "")) if cached is not None else None
        )
        if cached_provider_id not in {None, "", instance_id}:
            raise ProviderError(
                "E2B cached endpoint belongs to a different provider generation",
                code="endpoint_generation_changed",
                retryable=True,
                status_code=409,
            )
        deadline = self._clock() + self.config.status_retry_seconds
        last_error: Exception | None = None
        while True:
            try:
                sandbox = await self._with_rate_limit_retry(
                    lambda: self._sdk.sandbox_cls.connect(
                        instance_id,
                        timeout=self.config.timeout_seconds,
                        **self._api_options(),
                    )
                )
                break
            except (
                self._sdk.not_found_error,
                self._sdk.sandbox_error,
                httpx.TransportError,
            ) as exc:
                # E2B's connect control plane can lag the authenticated gateway
                # while the sandbox remains alive. Retry the exact provider ID
                # only; inventory, pause, and delete are deliberately excluded
                # from endpoint recovery.
                last_error = exc
                if self._clock() >= deadline:
                    if cached is not None:
                        # A gateway miss does not prove this exact sandbox is
                        # gone. Keep its authenticated endpoint and grant the
                        # transport another bounded retry window rather than
                        # converting route flapping into lifecycle churn.
                        logger.debug('agentbox.e2b.e2b_endpoint_refresh_control_plane.diagnostic', sandbox_id=sandbox_id, instance_id=instance_id)
                        return self._endpoint_from_sandbox(
                            sandbox_id,
                            cached,
                            app,
                        )
                    raise ProviderError(
                        "E2B endpoint route refresh is temporarily unavailable",
                        code="endpoint_routing_unavailable",
                        retryable=True,
                        status_code=503,
                        headers={"Retry-After": "1"},
                    ) from last_error
                await self._sleep(random.uniform(0.05, 0.2))
        provider_id = str(getattr(sandbox, "sandbox_id", ""))
        if provider_id != instance_id:
            raise ProviderError(
                "E2B endpoint refresh returned a different provider generation",
                code="endpoint_generation_changed",
                retryable=True,
                status_code=409,
            )
        self._sandboxes[sandbox_id] = sandbox
        self._known_infos[sandbox_id] = _E2BManagedInfo(
            provider_id=instance_id,
            metadata=known.metadata
            if known is not None
            else self._metadata(sandbox_id),
            state="running",
        )
        self._lease_renewed_at[sandbox_id] = self._clock()
        await self._record_capacity_observed(sandbox_id, instance_id)
        return self._endpoint_from_sandbox(sandbox_id, sandbox, app)

    @staticmethod
    def _endpoint_from_sandbox(
        sandbox_id: str,
        sandbox,
        app: SandboxAppSpec,
    ) -> SandboxEndpoint:
        token = getattr(sandbox, "traffic_access_token", None)
        if not token:
            raise ProviderError(
                "E2B traffic access token is missing", code="endpoint_auth_missing"
            )
        return SandboxEndpoint(
            base_url=f"https://{sandbox.get_host(app.port)}",
            headers={"e2b-traffic-access-token": str(token)},
            instance_id=str(getattr(sandbox, "sandbox_id", sandbox_id)),
            provider_id=str(getattr(sandbox, "sandbox_id", sandbox_id)),
            transient_gateway="e2b",
        )

    async def close(self) -> None:
        self._sandboxes.clear()
        self._known_infos.clear()
        self._lease_renewed_at.clear()
        self._lease_renewal_locks.clear()
