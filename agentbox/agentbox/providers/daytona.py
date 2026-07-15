from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any

from agentbox.apps import SANDBOX_APPS, SandboxAppSpec
from agentbox.schemas import (
    SandboxEnsureRequest,
    SandboxInternalAppStatus,
    SandboxInternalStatus,
)

from .cloud_config import DaytonaProviderConfig
from .errors import ProviderError, SandboxNotFoundError
from .legacy import LegacyRuntimeProviderMixin
from .models import EndpointProtocol, ManagedSandbox, SandboxEndpoint, SandboxRef


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _DaytonaSdk:
    client_cls: Any
    config_cls: Any
    query_cls: Any
    snapshot_params_cls: Any
    image_params_cls: Any
    error: type[Exception]
    not_found_error: type[Exception]


def _load_sdk() -> _DaytonaSdk:
    try:
        from daytona import (
            AsyncDaytona,
            CreateSandboxFromImageParams,
            CreateSandboxFromSnapshotParams,
            DaytonaConfig,
            DaytonaError,
            DaytonaNotFoundError,
            ListSandboxesQuery,
        )
    except ImportError as exc:  # pragma: no cover - installation diagnostic
        raise RuntimeError(
            "AGENTBOX_PROVIDER=daytona requires the 'agentbox[daytona]' extra"
        ) from exc
    return _DaytonaSdk(
        client_cls=AsyncDaytona,
        config_cls=DaytonaConfig,
        query_cls=ListSandboxesQuery,
        snapshot_params_cls=CreateSandboxFromSnapshotParams,
        image_params_cls=CreateSandboxFromImageParams,
        error=DaytonaError,
        not_found_error=DaytonaNotFoundError,
    )


class DaytonaSandboxProvider(LegacyRuntimeProviderMixin):
    """Daytona compute adapter using fresh private-preview credentials."""

    provider_name = "daytona"

    def __init__(
        self,
        config: DaytonaProviderConfig | None = None,
        *,
        sdk: _DaytonaSdk | None = None,
        client=None,
    ) -> None:
        self.config = config or DaytonaProviderConfig.from_env()
        self._sdk = sdk or _load_sdk()
        self.daytona = client or self._sdk.client_cls(
            self._sdk.config_cls(
                api_key=self.config.api_key,
                api_url=self.config.api_url,
                target=self.config.target,
            )
        )
        self._create_locks: dict[str, asyncio.Lock] = {}
        self._capacity_condition = asyncio.Condition()
        self._capacity_init_lock = asyncio.Lock()
        self._capacity_reservations: set[str] = set()
        self._counted_provider_ids: set[str] = set()
        self._capacity_generation = 0
        self._observed_active_count: int | None = None
        self._create_semaphore = asyncio.Semaphore(
            self.config.create_max_in_flight
        )
        self._create_rate_lock = asyncio.Lock()
        self._next_create_at = 0.0

    def _labels(self, sandbox_id: str | None = None) -> dict[str, str]:
        labels = {
            "managed-by": "agentbox",
            "agentbox-owner": self.config.owner,
        }
        if sandbox_id:
            labels["agentbox-id"] = sandbox_id
        return labels

    @staticmethod
    def _state(sandbox) -> str:
        state = getattr(sandbox, "state", "unknown")
        return str(getattr(state, "value", state)).lower()

    async def _iter(self, labels: dict[str, str]):
        query = self._sdk.query_cls(labels=labels)
        async for sandbox in self.daytona.list(query):
            yield sandbox

    async def _find(self, sandbox_id: str):
        try:
            async for sandbox in self._iter(self._labels(sandbox_id)):
                if (getattr(sandbox, "labels", None) or {}).get(
                    "agentbox-id"
                ) == sandbox_id:
                    await self._record_capacity_observed(
                        sandbox_id, str(getattr(sandbox, "id", ""))
                    )
                    return sandbox
        except self._sdk.not_found_error:
            pass
        return None

    async def _active_provider_ids(self) -> set[str]:
        provider_ids: set[str] = set()
        async for sandbox in self._iter(self._labels()):
            if self._state(sandbox) not in {"destroyed", "destroying"}:
                provider_ids.add(str(getattr(sandbox, "id", "")))
        return provider_ids

    def _clock(self) -> float:
        return asyncio.get_running_loop().time()

    async def _sleep(self, delay: float) -> None:
        await asyncio.sleep(delay)

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
                        "agentbox_daytona_capacity event=admission_timeout "
                        "sandbox_id=%s active=%s reserved=%s max_active=%s",
                        sandbox_id,
                        self._observed_active_count,
                        len(self._capacity_reservations),
                        self.config.max_active,
                    )
                    raise ProviderError(
                        f"Daytona concurrency limit ({self.config.max_active}) reached",
                        code="capacity_exhausted",
                        status_code=429,
                        headers={
                            "Retry-After": str(
                                self.config.capacity_retry_after_seconds
                            )
                        },
                    )
                try:
                    await asyncio.wait_for(
                        self._capacity_condition.wait(), timeout=remaining
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
            if created:
                if not provider_id:
                    raise RuntimeError(
                        "created Daytona reservation is missing provider ID"
                    )
                self._counted_provider_ids.add(provider_id)
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

    async def _record_capacity_removed(
        self, provider_id: str | None
    ) -> None:
        async with self._capacity_condition:
            if provider_id:
                self._counted_provider_ids.discard(provider_id)
            if self._observed_active_count is not None:
                self._observed_active_count = len(self._counted_provider_ids)
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

    async def _create_at_provider(self, params):  # type: ignore[no-untyped-def]
        async with self._create_semaphore:
            await self._wait_for_create_rate_slot()
            return await self.daytona.create(
                params,
                timeout=self.config.ready_timeout_seconds,
            )

    async def create(
        self, sandbox_id: str, request: SandboxEnsureRequest
    ) -> SandboxInternalStatus:
        lock = self._create_locks.setdefault(sandbox_id, asyncio.Lock())
        async with lock:
            existing = await self._find(sandbox_id)
            if existing is not None:
                state = self._state(existing)
                try:
                    if state == "archived":
                        await existing.recover(
                            timeout=self.config.ready_timeout_seconds
                        )
                    elif state in {"stopped", "paused"}:
                        await self.daytona.start(
                            existing,
                            timeout=self.config.ready_timeout_seconds,
                        )
                except self._sdk.error as exc:
                    raise ProviderError(
                        f"Daytona sandbox resume failed: {exc}", retryable=True
                    ) from exc
                return await self._status(sandbox_id, existing)

            await self._reserve_capacity(sandbox_id)
            reservation_finished = False
            try:
                common = {
                    "env_vars": request.env,
                    "labels": self._labels(sandbox_id),
                    "public": False,
                    "auto_stop_interval": self.config.auto_stop_minutes,
                    "auto_archive_interval": 0,
                    "auto_delete_interval": self.config.auto_delete_minutes,
                }
                params = (
                    self._sdk.snapshot_params_cls(
                        snapshot=self.config.snapshot, **common
                    )
                    if self.config.snapshot
                    else self._sdk.image_params_cls(image=self.config.image, **common)
                )
                try:
                    sandbox = await self._create_at_provider(params)
                except self._sdk.error as exc:
                    raise ProviderError(
                        f"Daytona sandbox creation failed: {exc}", retryable=True
                    ) from exc
                await self._finish_reservation(
                    sandbox_id,
                    created=True,
                    provider_id=str(getattr(sandbox, "id", "")),
                )
                reservation_finished = True
                return await self._status(sandbox_id, sandbox)
            finally:
                if not reservation_finished:
                    await self._finish_reservation(sandbox_id, created=False)

    async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
        sandbox = await self._find(sandbox_id)
        if sandbox is None:
            raise SandboxNotFoundError(sandbox_id)
        return await self._status(sandbox_id, sandbox)

    async def _status(self, sandbox_id: str, sandbox) -> SandboxInternalStatus:
        state = self._state(sandbox)
        ready = state == "started"
        status = (
            "RUNNING"
            if ready
            else "CREATING"
            if state in {"creating", "restoring", "starting", "resuming"}
            else "STOPPED"
            if state in {"stopped", "archived", "paused"}
            else "ERROR"
        )
        return SandboxInternalStatus(
            id=sandbox_id,
            ready=ready,
            status=status,
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

    async def list_managed(self) -> list[ManagedSandbox]:
        managed: list[ManagedSandbox] = []
        async for sandbox in self._iter(self._labels()):
            labels = dict(getattr(sandbox, "labels", None) or {})
            sandbox_id = labels.get("agentbox-id")
            if not sandbox_id:
                continue
            provider_id = str(getattr(sandbox, "id", sandbox_id))
            managed.append(
                ManagedSandbox(
                    ref=SandboxRef(sandbox_id, provider_id),
                    status=await self._status(sandbox_id, sandbox),
                    instance_id=self._instance_id(sandbox),
                    metadata=labels,
                )
            )
        return managed

    @staticmethod
    def _instance_id(sandbox) -> str:
        provider_id = str(getattr(sandbox, "id", "unknown"))
        updated_at = getattr(sandbox, "updated_at", None)
        return f"{provider_id}:{updated_at}" if updated_at else provider_id

    async def delete(self, sandbox_id: str) -> bool:
        sandbox = await self._find(sandbox_id)
        if sandbox is None:
            return False
        provider_id = str(getattr(sandbox, "id", ""))
        try:
            await self.daytona.delete(sandbox)
            await self._record_capacity_removed(provider_id)
            return True
        except self._sdk.not_found_error:
            await self._record_capacity_removed(provider_id)
            return False
        except self._sdk.error as exc:
            raise ProviderError(
                f"Daytona sandbox deletion failed: {exc}", retryable=True
            ) from exc

    async def resolve_endpoint(
        self,
        sandbox_id: str,
        app: SandboxAppSpec,
        *,
        protocol: EndpointProtocol = "http",
    ) -> SandboxEndpoint:
        del protocol
        sandbox = await self._find(sandbox_id)
        if sandbox is None:
            raise SandboxNotFoundError(sandbox_id)
        try:
            preview = await sandbox.get_preview_link(app.port)
        except self._sdk.error as exc:
            raise ProviderError(
                f"Daytona preview authentication failed: {exc}", retryable=True
            ) from exc
        url = getattr(preview, "url", None)
        token = getattr(preview, "token", None)
        if not url or not token:
            raise ProviderError(
                "Daytona preview URL or token is missing",
                code="endpoint_auth_missing",
            )
        return SandboxEndpoint(
            base_url=str(url),
            headers={
                "X-Daytona-Preview-Token": str(token),
                "X-Daytona-Skip-Preview-Warning": "true",
            },
            instance_id=self._instance_id(sandbox),
        )

    async def close(self) -> None:
        close = getattr(self.daytona, "close", None)
        if close is not None:
            await close()
