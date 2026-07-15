from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import random
from dataclasses import dataclass
from typing import Any

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


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _E2BSdk:
    sandbox_cls: Any
    query_cls: Any
    rate_limit_error: type[Exception]
    not_found_error: type[Exception]
    sandbox_error: type[Exception]


@dataclass(frozen=True)
class _E2BManagedInfo:
    provider_id: str
    metadata: dict[str, str]
    state: str


def _load_sdk() -> _E2BSdk:
    try:
        from e2b import (
            AsyncSandbox,
            RateLimitException,
            SandboxException,
            SandboxNotFoundException,
            SandboxQuery,
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
        self._create_semaphore = asyncio.Semaphore(
            self.config.create_max_in_flight
        )
        self._create_in_flight = 0
        self._create_rate_lock = asyncio.Lock()
        self._next_create_at = 0.0

    @property
    def capacity_policy(self) -> ProviderCapacityPolicy:
        return ProviderCapacityPolicy(
            scope=(
                f"{self.provider_name}:{self.config.owner}:"
                f"{self.config.environment}"
            ),
            max_active=self.config.max_active,
        )

    def _api_options(self) -> dict[str, str]:
        options = {"api_key": self.config.api_key}
        if self.config.api_url:
            options["api_url"] = self.config.api_url
        if self.config.domain:
            options["domain"] = self.config.domain
        return options

    def _metadata(self, sandbox_id: str | None = None) -> dict[str, str]:
        metadata = {
            "managed-by": "agentbox",
            "agentbox-owner": self.config.owner,
            "agentbox-environment": self.config.environment,
        }
        if sandbox_id:
            metadata["agentbox-id"] = sandbox_id
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
                    seconds = (
                        retry_at - datetime.now(timezone.utc)
                    ).total_seconds()
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
                            "Retry-After": str(
                                self.config.capacity_retry_after_seconds
                            )
                        },
                    ) from exc
                provider_delay = self._provider_retry_after(exc)
                base_delay = (
                    provider_delay
                    if provider_delay is not None
                    else max(
                        fallback_delay,
                        float(self.config.capacity_retry_after_seconds),
                    )
                )
                jitter = random.uniform(
                    0.0, min(max(base_delay * 0.2, 0.05), 2.0)
                )
                delay = base_delay + jitter
                logger.warning(
                    "agentbox_e2b_rate_limit attempt=%d "
                    "retry_after_seconds=%.3f provider_retry_after=%s",
                    attempt + 1,
                    delay,
                    provider_delay,
                )
                await self._sleep(delay)
                fallback_delay = min(fallback_delay * 2, 8)
        raise RuntimeError("unreachable")

    @staticmethod
    def _network_policy() -> dict[str, object]:
        return {
            "allow_public_traffic": False,
            "deny_out": [
                "127.0.0.0/8",
                "10.0.0.0/8",
                "100.64.0.0/10",
                "169.254.0.0/16",
                "172.16.0.0/12",
                "192.168.0.0/16",
                "::1/128",
                "fc00::/7",
                "fe80::/10",
            ],
        }

    async def _list(self, metadata: dict[str, str]):
        async with self._list_lock:
            paginator = self._sdk.sandbox_cls.list(
                query=self._sdk.query_cls(metadata=metadata),
                limit=100,
                **self._api_options(),
            )
            while paginator.has_next:
                for item in await self._with_rate_limit_retry(paginator.next_items):
                    yield item

    async def _find(self, sandbox_id: str):
        cached = self._known_infos.get(sandbox_id)
        if cached is not None:
            return cached
        try:
            async for info in self._list(self._metadata(sandbox_id)):
                normalized = self._normalize_info(info)
                metadata = normalized.metadata
                if metadata.get("agentbox-id") == sandbox_id:
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

    async def _connect(
        self,
        sandbox_id: str,
        info: _E2BManagedInfo | None = None,
        *,
        resume: bool = False,
    ):
        cached = self._sandboxes.get(sandbox_id)
        if cached is not None:
            return cached
        info = info or await self._find(sandbox_id)
        if info is None:
            return None
        if info.state != "running" and not resume:
            raise ProviderError(
                f"E2B sandbox {sandbox_id} is suspended",
                code="sandbox_suspended",
                status_code=409,
            )
        sandbox = await self._with_rate_limit_retry(
            lambda: self._sdk.sandbox_cls.connect(
                info.provider_id,
                timeout=self.config.timeout_seconds,
                **self._api_options(),
            )
        )
        self._sandboxes[sandbox_id] = sandbox
        self._known_infos[sandbox_id] = _E2BManagedInfo(
            provider_id=info.provider_id,
            metadata=info.metadata,
            state="running",
        )
        await self._record_capacity_observed(
            sandbox_id, getattr(sandbox, "sandbox_id", None)
        )
        return sandbox

    async def _active_provider_ids(self) -> set[str]:
        provider_ids: set[str] = set()
        async for info in self._list(self._metadata()):
            normalized = self._normalize_info(info)
            if normalized.state == "running":
                provider_ids.add(normalized.provider_id)
        return provider_ids

    def _log_capacity(
        self,
        event: str,
        *,
        sandbox_id: str | None = None,
        level: int = logging.INFO,
        **values: object,
    ) -> None:
        fields = {
            "event": event,
            "sandbox_id": sandbox_id,
            "active": self._observed_active_count,
            "reserved": len(self._capacity_reservations),
            "max_active": self.config.max_active,
            "create_in_flight": self._create_in_flight,
            **values,
        }
        logger.log(
            level,
            "agentbox_e2b_capacity %s",
            " ".join(f"{key}={value}" for key, value in fields.items()),
        )

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
                    self._log_capacity("initialized")

    async def _reserve_capacity(self, sandbox_id: str) -> None:
        await self._initialize_capacity()
        deadline = self._clock() + self.config.admission_wait_seconds
        async with self._capacity_condition:
            while (self._observed_active_count or 0) + len(
                self._capacity_reservations
            ) >= self.config.max_active:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    self._log_capacity(
                        "admission_timeout",
                        sandbox_id=sandbox_id,
                        level=logging.WARNING,
                        waited_seconds=self.config.admission_wait_seconds,
                    )
                    raise ProviderError(
                        f"E2B concurrency limit ({self.config.max_active}) reached",
                        code="capacity_exhausted",
                        retryable=True,
                        status_code=429,
                        headers={
                            "Retry-After": str(
                                self.config.capacity_retry_after_seconds
                            )
                        },
                    )
                self._log_capacity(
                    "admission_wait",
                    sandbox_id=sandbox_id,
                    remaining_seconds=round(remaining, 3),
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
            self._log_capacity("reserved", sandbox_id=sandbox_id)

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
            self._log_capacity(
                "create_committed" if created else "reservation_released",
                sandbox_id=sandbox_id,
            )

    async def _mark_reservation_ambiguous(self, sandbox_id: str) -> None:
        """Retain admission until a successful inventory resolves uncertainty."""

        async with self._capacity_condition:
            if sandbox_id in self._capacity_reservations:
                self._ambiguous_capacity_reservations.add(sandbox_id)
                self._capacity_generation += 1
                self._log_capacity(
                    "reservation_ambiguous",
                    sandbox_id=sandbox_id,
                    level=logging.WARNING,
                )

    async def _record_capacity_removed(
        self, sandbox_id: str, provider_id: str | None
    ) -> None:
        async with self._capacity_condition:
            was_counted = bool(
                provider_id and provider_id in self._counted_provider_ids
            )
            if provider_id:
                self._counted_provider_ids.discard(provider_id)
            if self._observed_active_count is not None:
                self._observed_active_count = len(self._counted_provider_ids)
            self._capacity_generation += 1
            self._capacity_condition.notify_all()
            self._log_capacity(
                "removed",
                sandbox_id=sandbox_id,
                provider_id=provider_id,
                counted=was_counted,
            )

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
            self._log_capacity("observed_existing", provider_id=provider_id)

    async def _refresh_capacity_inventory(self, event: str) -> None:
        async with self._capacity_condition:
            inventory_generation = self._capacity_generation
        normalized_infos = [
            self._normalize_info(info)
            async for info in self._list(self._metadata())
        ]
        provider_ids = {
            info.provider_id for info in normalized_infos if info.state == "running"
        }
        async with self._capacity_condition:
            if (
                inventory_generation == self._capacity_generation
                and not self._capacity_reservations
            ):
                self._counted_provider_ids = provider_ids
                self._observed_active_count = len(provider_ids)
                self._known_infos = {
                    info.metadata["agentbox-id"]: info
                    for info in normalized_infos
                    if info.metadata.get("agentbox-id")
                }
                self._capacity_generation += 1
                self._log_capacity(event)
            else:
                self._log_capacity(f"{event}_stale")
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
                self._log_capacity("provider_create_start", sandbox_id=sandbox_id)
            try:
                async def rate_limited_operation():
                    await self._wait_for_create_rate_slot()
                    return await operation()

                return await self._with_rate_limit_retry(rate_limited_operation)
            finally:
                async with self._capacity_condition:
                    self._create_in_flight -= 1
                    self._capacity_condition.notify_all()
                    self._log_capacity("provider_create_finish", sandbox_id=sandbox_id)

    async def bootstrap(
        self,
        sandbox_id: str,
        request: SandboxEnsureRequest,
    ) -> None:
        sandbox = await self._connect(sandbox_id, resume=True)
        if sandbox is None:
            raise SandboxNotFoundError(sandbox_id)
        await self._bootstrap_sandbox(sandbox, request.env)

    async def _bootstrap_sandbox(self, sandbox, env: dict[str, str]) -> None:
        """Start the runtime after create-time env exists in the sandbox.

        E2B template start commands run during the build snapshot and cannot
        see Sandbox.create(envs=...). The provider hook is intentionally narrow:
        it starts the common runtime entrypoint but never implements runtime
        sessions, function execution, or app transport.
        """

        processes = await self._with_rate_limit_retry(sandbox.commands.list)
        running = any(
            "/usr/local/bin/start-runtime"
            in " ".join(
                [str(getattr(process, "cmd", "")), *map(str, getattr(process, "args", []))]
            )
            for process in processes
        )
        if running:
            return
        await self._with_rate_limit_retry(
            lambda: sandbox.commands.run(
                "/usr/local/bin/start-runtime",
                background=True,
                envs=env,
                user=self.config.runtime_user,
                timeout=self.config.runtime_bootstrap_timeout_seconds,
            )
        )

    async def create(
        self, sandbox_id: str, request: SandboxEnsureRequest
    ) -> SandboxInternalStatus:
        lock = self._create_locks.setdefault(sandbox_id, asyncio.Lock())
        async with lock:
            existing = await self._find(sandbox_id)
            if existing is not None:
                if existing.state == "running":
                    sandbox = await self._connect(sandbox_id, existing)
                    if sandbox is not None:
                        await sandbox.set_timeout(
                            self.config.timeout_seconds, **self._api_options()
                        )
                        await self._bootstrap_sandbox(sandbox, request.env)
                        return await self._status(sandbox_id, sandbox)
                else:
                    await self._reserve_capacity(sandbox_id)
                    resumed = False
                    try:
                        sandbox = await self._connect(
                            sandbox_id, existing, resume=True
                        )
                        if sandbox is None:
                            raise SandboxNotFoundError(sandbox_id)
                        await self._finish_reservation(
                            sandbox_id,
                            created=True,
                            provider_id=existing.provider_id,
                        )
                        resumed = True
                        await self._bootstrap_sandbox(sandbox, request.env)
                        return await self._status(sandbox_id, sandbox)
                    except Exception:
                        if resumed:
                            await self.release(sandbox_id)
                        raise
                    finally:
                        if not resumed:
                            await self._finish_reservation(
                                sandbox_id, created=False
                            )

            await self._reserve_capacity(sandbox_id)
            reservation_finished = False
            sandbox = None
            try:
                try:
                    async def create_at_provider():
                        return await self._sdk.sandbox_cls.create(
                            self.config.template,
                            timeout=self.config.timeout_seconds,
                            metadata=self._metadata(sandbox_id),
                            envs=request.env,
                            secure=True,
                            allow_internet_access=self.config.allow_internet_access,
                            network=self._network_policy(),
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
                except self._sdk.sandbox_error as exc:
                    # A transport timeout can happen after E2B accepted create.
                    # Re-list by our scoped logical ID before freeing capacity.
                    try:
                        info = await self._find(sandbox_id)
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
                        raise ProviderError(
                            f"E2B sandbox creation failed: {exc}",
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
                provider_id = getattr(sandbox, "sandbox_id", None)
                await self._finish_reservation(
                    sandbox_id,
                    created=True,
                    provider_id=provider_id,
                )
                reservation_finished = True
                try:
                    await self._bootstrap_sandbox(sandbox, request.env)
                    return await self._status(sandbox_id, sandbox)
                except Exception:
                    await self._discard_provider(sandbox_id, provider_id)
                    raise
            finally:
                if not reservation_finished:
                    await self._finish_reservation(sandbox_id, created=False)

    async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
        sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is not None:
            status = await self._status(sandbox_id, sandbox)
            if status.ready:
                return status
            self.invalidate_sandbox_cache(sandbox_id)
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
        try:
            running = bool(await sandbox.is_running())
        except self._sdk.not_found_error as exc:
            self.invalidate_sandbox_cache(sandbox_id)
            raise SandboxNotFoundError(sandbox_id) from exc
        except self._sdk.sandbox_error as exc:
            raise ProviderError(
                f"E2B sandbox status failed: {exc}", retryable=True
            ) from exc
        apps = {
            app.name: SandboxInternalAppStatus(
                name=app.name,
                public_slug=app.public_slug,
                port=app.port,
                ready=running,
                private_url=(f"https://{sandbox.get_host(app.port)}" if running else None),
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
        async with self._capacity_condition:
            ambiguous_at_start = tuple(self._ambiguous_capacity_reservations)
        managed: list[ManagedSandbox] = []
        async for raw_info in self._list(self._metadata()):
            info = self._normalize_info(raw_info)
            metadata = info.metadata
            sandbox_id = metadata.get("agentbox-id")
            if not sandbox_id:
                continue
            self._known_infos[sandbox_id] = info
            managed.append(
                ManagedSandbox(
                    ref=SandboxRef(sandbox_id, info.provider_id),
                    status=self._status_from_info(sandbox_id, info),
                    instance_id=info.provider_id,
                    metadata=metadata,
                )
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
            await self._refresh_capacity_inventory("release_not_found")
            return False
        if info.state != "running":
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
            await self._refresh_capacity_inventory("release_not_found")
            return False
        except self._sdk.sandbox_error as exc:
            raise ProviderError(
                f"E2B sandbox release failed: {exc}", retryable=True
            ) from exc
        self._sandboxes.pop(sandbox_id, None)
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
            await self._refresh_capacity_inventory("delete_not_found")
            return False
        try:
            deleted = bool(
                await self._with_rate_limit_retry(
                    lambda: self._sdk.sandbox_cls.kill(
                        info.provider_id, **self._api_options()
                    )
                )
            )
            await self._record_capacity_removed(
                sandbox_id, info.provider_id
            )
            self._known_infos.pop(sandbox_id, None)
            return deleted
        except self._sdk.not_found_error:
            await self._record_capacity_removed(
                sandbox_id, info.provider_id
            )
            self._known_infos.pop(sandbox_id, None)
            return False
        except self._sdk.sandbox_error as exc:
            raise ProviderError(
                f"E2B sandbox deletion failed: {exc}", retryable=True
            ) from exc

    async def _discard_provider(
        self, sandbox_id: str, provider_id: str | None = None
    ) -> None:
        cached = self._sandboxes.pop(sandbox_id, None)
        self._known_infos.pop(sandbox_id, None)
        provider_id = provider_id or getattr(cached, "sandbox_id", None)
        if not provider_id:
            await self._refresh_capacity_inventory("discard_missing_id")
            raise ProviderError(
                "E2B cleanup outcome is unknown",
                code="provider_cleanup_outcome_unknown",
                retryable=True,
            )
        try:
            await self._with_rate_limit_retry(
                lambda: self._sdk.sandbox_cls.kill(
                    provider_id, **self._api_options()
                )
            )
        except self._sdk.not_found_error:
            pass
        except Exception as exc:
            logger.exception(
                "Failed to discard unbootstrapped E2B sandbox %s",
                sandbox_id,
            )
            raise ProviderError(
                "E2B cleanup outcome is unknown",
                code="provider_cleanup_outcome_unknown",
                retryable=True,
            ) from exc
        await self._record_capacity_removed(sandbox_id, provider_id)

    async def purge_managed(self, ref: SandboxRef) -> bool:
        """Purge one exact provider generation without targeting a replacement."""

        try:
            deleted = bool(
                await self._with_rate_limit_retry(
                    lambda: self._sdk.sandbox_cls.kill(
                        ref.provider_id, **self._api_options()
                    )
                )
            )
        except self._sdk.not_found_error:
            deleted = False
        except self._sdk.sandbox_error as exc:
            raise ProviderError(
                f"E2B managed sandbox purge failed: {exc}", retryable=True
            ) from exc
        known = self._known_infos.get(ref.sandbox_id)
        if known is None or known.provider_id == ref.provider_id:
            self.invalidate_sandbox_cache(ref.sandbox_id)
        await self._record_capacity_removed(ref.sandbox_id, ref.provider_id)
        return deleted

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
        token = getattr(sandbox, "traffic_access_token", None)
        if not token:
            raise ProviderError(
                "E2B traffic access token is missing", code="endpoint_auth_missing"
            )
        return SandboxEndpoint(
            base_url=f"https://{sandbox.get_host(app.port)}",
            headers={"e2b-traffic-access-token": str(token)},
            instance_id=str(getattr(sandbox, "sandbox_id", sandbox_id)),
        )

    async def close(self) -> None:
        self._sandboxes.clear()
        self._known_infos.clear()
