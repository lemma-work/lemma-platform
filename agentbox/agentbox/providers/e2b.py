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
from .models import EndpointProtocol, ManagedSandbox, SandboxEndpoint, SandboxRef


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _E2BSdk:
    sandbox_cls: Any
    query_cls: Any
    rate_limit_error: type[Exception]
    not_found_error: type[Exception]
    sandbox_error: type[Exception]


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
        self._capacity_condition = asyncio.Condition()
        self._capacity_init_lock = asyncio.Lock()
        self._capacity_reservations: set[str] = set()
        self._counted_provider_ids: set[str] = set()
        self._capacity_generation = 0
        self._observed_active_count: int | None = None
        self._create_semaphore = asyncio.Semaphore(
            self.config.create_max_in_flight
        )
        self._create_in_flight = 0
        self._create_rate_lock = asyncio.Lock()
        self._next_create_at = 0.0

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
        try:
            async for info in self._list(self._metadata(sandbox_id)):
                metadata = getattr(info, "metadata", None) or {}
                if metadata.get("agentbox-id") == sandbox_id:
                    return info
        except self._sdk.not_found_error:
            pass
        return None

    async def _connect(self, sandbox_id: str, info=None):
        info = info or await self._find(sandbox_id)
        if info is None:
            return None
        cached = self._sandboxes.get(sandbox_id)
        if cached is not None and getattr(cached, "sandbox_id", None) == info.sandbox_id:
            await self._record_capacity_observed(
                sandbox_id, getattr(cached, "sandbox_id", None)
            )
            return cached
        sandbox = await self._with_rate_limit_retry(
            lambda: self._sdk.sandbox_cls.connect(
                info.sandbox_id,
                timeout=self.config.timeout_seconds,
                **self._api_options(),
            )
        )
        self._sandboxes[sandbox_id] = sandbox
        await self._record_capacity_observed(
            sandbox_id, getattr(sandbox, "sandbox_id", None)
        )
        return sandbox

    async def _active_provider_ids(self) -> set[str]:
        provider_ids: set[str] = set()
        async for info in self._list(self._metadata()):
            provider_ids.add(str(info.sandbox_id))
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
        provider_ids = await self._active_provider_ids()
        async with self._capacity_condition:
            if (
                inventory_generation == self._capacity_generation
                and not self._capacity_reservations
            ):
                self._counted_provider_ids = provider_ids
                self._observed_active_count = len(provider_ids)
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
        sandbox = await self._connect(sandbox_id)
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
                sandbox = await self._connect(sandbox_id, existing)
                if sandbox is not None:
                    await sandbox.set_timeout(
                        self.config.timeout_seconds, **self._api_options()
                    )
                    await self._bootstrap_sandbox(sandbox, request.env)
                    return await self._status(sandbox_id, sandbox)

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
                            lifecycle={"on_timeout": "kill", "auto_resume": False},
                            **self._api_options(),
                        )

                    sandbox = await self._create_provider_sandbox(
                        create_at_provider, sandbox_id
                    )
                except self._sdk.sandbox_error as exc:
                    raise ProviderError(
                        f"E2B sandbox creation failed: {exc}", retryable=True
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
        sandbox = await self._connect(sandbox_id)
        if sandbox is None:
            raise SandboxNotFoundError(sandbox_id)
        return await self._status(sandbox_id, sandbox)

    async def _status(self, sandbox_id: str, sandbox) -> SandboxInternalStatus:
        try:
            running = bool(await sandbox.is_running())
        except self._sdk.not_found_error as exc:
            self._sandboxes.pop(sandbox_id, None)
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
        managed: list[ManagedSandbox] = []
        async for info in self._list(self._metadata()):
            metadata = dict(getattr(info, "metadata", None) or {})
            sandbox_id = metadata.get("agentbox-id")
            if not sandbox_id:
                continue
            sandbox = await self._connect(sandbox_id, info)
            if sandbox is None:
                continue
            managed.append(
                ManagedSandbox(
                    ref=SandboxRef(sandbox_id, str(info.sandbox_id)),
                    status=await self._status(sandbox_id, sandbox),
                    instance_id=str(info.sandbox_id),
                    metadata=metadata,
                )
            )
        return managed

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
                        info.sandbox_id, **self._api_options()
                    )
                )
            )
            await self._record_capacity_removed(
                sandbox_id, str(info.sandbox_id)
            )
            return deleted
        except self._sdk.not_found_error:
            await self._record_capacity_removed(
                sandbox_id, str(info.sandbox_id)
            )
            return False
        except self._sdk.sandbox_error as exc:
            raise ProviderError(
                f"E2B sandbox deletion failed: {exc}", retryable=True
            ) from exc

    async def _discard_provider(
        self, sandbox_id: str, provider_id: str | None = None
    ) -> None:
        cached = self._sandboxes.pop(sandbox_id, None)
        provider_id = provider_id or getattr(cached, "sandbox_id", None)
        try:
            if provider_id:
                try:
                    await self._with_rate_limit_retry(
                        lambda: self._sdk.sandbox_cls.kill(
                            provider_id, **self._api_options()
                        )
                    )
                except self._sdk.not_found_error:
                    pass
                except Exception:
                    logger.exception(
                        "Failed to discard unbootstrapped E2B sandbox %s",
                        sandbox_id,
                    )
        finally:
            await self._record_capacity_removed(sandbox_id, provider_id)

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
