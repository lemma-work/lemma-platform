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
        if (
            not self.config.network_allow_list
            and not self.config.domain_allow_list
            and not self.config.allow_unsafe_private_egress
        ):
            raise RuntimeError(
                "Daytona cannot enforce private-network egress denial; configure "
                "DAYTONA_NETWORK_ALLOW_LIST or DAYTONA_DOMAIN_ALLOW_LIST, or explicitly "
                "set AGENTBOX_ALLOW_UNSAFE_PRIVATE_EGRESS=true"
            )
        self._sdk = sdk or _load_sdk()
        self.daytona = client or self._sdk.client_cls(
            self._sdk.config_cls(
                api_key=self.config.api_key,
                api_url=self.config.api_url,
                target=self.config.target,
            )
        )
        self._create_locks: dict[str, asyncio.Lock] = {}
        self._sandboxes: dict[str, object] = {}
        self._capacity_condition = asyncio.Condition()
        self._capacity_init_lock = asyncio.Lock()
        self._capacity_reservations: set[str] = set()
        self._ambiguous_capacity_reservations: set[str] = set()
        self._counted_provider_ids: set[str] = set()
        self._capacity_generation = 0
        self._observed_active_count: int | None = None
        self._create_semaphore = asyncio.Semaphore(self.config.create_max_in_flight)
        self._create_rate_lock = asyncio.Lock()
        self._next_create_at = 0.0

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            stable_release_identity=True,
            release_preserves_filesystem=True,
            private_egress_isolation=bool(
                self.config.network_allow_list or self.config.domain_allow_list
            ),
            authenticated_http=True,
            authenticated_websocket=True,
        )

    @property
    def capacity_policy(self) -> ProviderCapacityPolicy:
        return ProviderCapacityPolicy(
            scope=(
                f"{self.provider_name}:{self.config.owner}:{self.config.environment}"
            ),
            max_active=self.config.max_active,
        )

    def _labels(
        self,
        sandbox_id: str | None = None,
        generation_token: str | None = None,
    ) -> dict[str, str]:
        labels = {
            "managed-by": "agentbox",
            "agentbox-owner": self.config.owner,
            "agentbox-environment": self.config.environment,
        }
        if sandbox_id:
            labels["agentbox-id"] = sandbox_id
        if generation_token:
            labels["agentbox-generation"] = generation_token
        return labels

    @staticmethod
    def _state(sandbox) -> str:
        state = getattr(sandbox, "state", "unknown")
        return str(getattr(state, "value", state)).lower()

    async def _iter(self, labels: dict[str, str]):
        query = self._sdk.query_cls(labels=labels)
        async for sandbox in self.daytona.list(query):
            yield sandbox

    async def _find(
        self,
        sandbox_id: str,
        generation_token: str | None = None,
        *,
        validate_cached: bool = False,
    ):
        cached = self._sandboxes.get(sandbox_id)
        cached_labels = dict(getattr(cached, "labels", None) or {})
        if (
            cached is not None
            and not validate_cached
            and (
                generation_token is None
                or cached_labels.get("agentbox-generation") == generation_token
            )
        ):
            return cached
        try:
            async for sandbox in self._iter(self._labels(sandbox_id, generation_token)):
                labels = dict(getattr(sandbox, "labels", None) or {})
                if labels.get("agentbox-id") == sandbox_id and (
                    generation_token is None
                    or labels.get("agentbox-generation") == generation_token
                ):
                    if self._state(sandbox) in {
                        "started",
                        "creating",
                        "restoring",
                        "starting",
                        "resuming",
                    }:
                        await self._record_capacity_observed(
                            sandbox_id,
                            str(getattr(sandbox, "id", "")),
                        )
                    self._sandboxes[sandbox_id] = sandbox
                    return sandbox
        except self._sdk.not_found_error:
            pass
        if validate_cached:
            self.invalidate_sandbox_cache(sandbox_id)
        return None

    def invalidate_sandbox_cache(self, sandbox_id: str) -> None:
        self._sandboxes.pop(sandbox_id, None)

    async def adopt(self, sandbox_id: str, provider_id: str) -> bool:
        """Adopt only the exact durable Daytona generation; never replace it."""

        cached = self._sandboxes.get(sandbox_id)
        if cached is not None:
            cached_id = str(getattr(cached, "id", ""))
            if cached_id != provider_id:
                self.invalidate_sandbox_cache(sandbox_id)
                sandbox = None
            else:
                sandbox = cached
        else:
            sandbox = None
        if sandbox is None:
            try:
                async for candidate in self._iter(self._labels(sandbox_id)):
                    if str(getattr(candidate, "id", "")) == provider_id:
                        sandbox = candidate
                        break
            except self._sdk.error as exc:
                raise ProviderError(
                    "Daytona durable provider generation could not be reconnected",
                    code="provider_adoption_unavailable",
                    retryable=True,
                    status_code=503,
                ) from exc
            if sandbox is None:
                # An empty eventually-consistent inventory cannot prove that a
                # previously recorded generation is gone.
                raise ProviderError(
                    "Daytona durable provider generation is not yet observable",
                    code="provider_adoption_unavailable",
                    retryable=True,
                    status_code=503,
                    headers={"Retry-After": "1"},
                )
            self._sandboxes[sandbox_id] = sandbox

        state = self._state(sandbox)
        try:
            if state == "error" and self._is_recoverable(sandbox):
                await sandbox.recover(timeout=self.config.ready_timeout_seconds)
            elif state in {"stopped", "paused", "archived"}:
                await self.daytona.start(
                    sandbox,
                    timeout=self.config.ready_timeout_seconds,
                )
        except (self._sdk.not_found_error, self._sdk.error) as exc:
            raise ProviderError(
                "Daytona durable provider generation could not be resumed",
                code="provider_adoption_unavailable",
                retryable=True,
                status_code=503,
            ) from exc
        return True

    def _commit_complete_inventory(
        self,
        observed: dict[str, object],
        *,
        cache_at_start: dict[str, object],
    ) -> None:
        """Atomically prune stale scoped cache after complete inventory."""

        committed = dict(observed)
        for sandbox_id, sandbox in self._sandboxes.items():
            if cache_at_start.get(sandbox_id) is not sandbox:
                committed[sandbox_id] = sandbox
        for sandbox_id in cache_at_start:
            if sandbox_id not in self._sandboxes:
                committed.pop(sandbox_id, None)
        self._sandboxes = committed

    async def _active_provider_ids(self) -> set[str]:
        provider_ids: set[str] = set()
        async for sandbox in self._iter(self._labels()):
            if self._state(sandbox) in {
                "started",
                "creating",
                "restoring",
                "starting",
                "resuming",
            }:
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
                        retryable=True,
                        status_code=429,
                        headers={
                            "Retry-After": str(self.config.capacity_retry_after_seconds)
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
            self._ambiguous_capacity_reservations.discard(sandbox_id)
            if created:
                if not provider_id:
                    raise RuntimeError(
                        "created Daytona reservation is missing provider ID"
                    )
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

    async def _record_capacity_removed(self, provider_id: str | None) -> None:
        async with self._capacity_condition:
            if provider_id:
                self._counted_provider_ids.discard(provider_id)
            if self._observed_active_count is not None:
                self._observed_active_count = len(self._counted_provider_ids)
            self._capacity_generation += 1
            self._capacity_condition.notify_all()

    async def _refresh_capacity_inventory(self) -> None:
        cache_at_start = dict(self._sandboxes)
        async with self._capacity_condition:
            inventory_generation = self._capacity_generation
        sandboxes = [sandbox async for sandbox in self._iter(self._labels())]
        provider_ids = {
            str(getattr(sandbox, "id", ""))
            for sandbox in sandboxes
            if self._state(sandbox)
            in {"started", "creating", "restoring", "starting", "resuming"}
        }
        observed = {
            str((getattr(sandbox, "labels", None) or {})["agentbox-id"]): sandbox
            for sandbox in sandboxes
            if (getattr(sandbox, "labels", None) or {}).get("agentbox-id")
        }
        self._commit_complete_inventory(observed, cache_at_start=cache_at_start)
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
            existing = await self._find(
                sandbox_id,
                generation_token,
                validate_cached=True,
            )
            if existing is not None:
                state = self._state(existing)
                if state == "started":
                    return await self._status(sandbox_id, existing)
                should_resume = state in {"stopped", "paused", "archived"}
                should_recover = state == "error" and self._is_recoverable(existing)
                if not should_resume and not should_recover:
                    return await self._status(sandbox_id, existing)
                await self._reserve_capacity(sandbox_id)
                resumed = False
                missing = False
                try:
                    if should_recover:
                        await existing.recover(
                            timeout=self.config.ready_timeout_seconds
                        )
                    else:
                        await self.daytona.start(
                            existing,
                            timeout=self.config.ready_timeout_seconds,
                        )
                except self._sdk.not_found_error:
                    self.invalidate_sandbox_cache(sandbox_id)
                    missing = True
                except self._sdk.error as exc:
                    raise ProviderError(
                        f"Daytona sandbox resume failed: {exc}", retryable=True
                    ) from exc
                finally:
                    if self._state(existing) == "started":
                        await self._finish_reservation(
                            sandbox_id,
                            created=True,
                            provider_id=str(getattr(existing, "id", "")),
                        )
                        resumed = True
                    if not resumed:
                        await self._finish_reservation(sandbox_id, created=False)
                if not missing:
                    self._sandboxes[sandbox_id] = existing
                    return await self._status(sandbox_id, existing)

            await self._reserve_capacity(sandbox_id)
            reservation_finished = False
            try:
                common = {
                    "env_vars": request.env,
                    "labels": self._labels(sandbox_id, generation_token),
                    "public": False,
                    "auto_stop_interval": self.config.auto_stop_minutes,
                    "auto_archive_interval": self.config.auto_archive_minutes,
                    "auto_delete_interval": self.config.auto_delete_minutes,
                    "network_block_all": bool(
                        self.config.network_allow_list or self.config.domain_allow_list
                    ),
                    "network_allow_list": ",".join(self.config.network_allow_list)
                    or None,
                    "domain_allow_list": ",".join(self.config.domain_allow_list)
                    or None,
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
                    # AsyncDaytona.create combines the POST with subsequent
                    # readiness polling. Even a DaytonaError carrying 4xx can
                    # therefore be post-acceptance (for example a transient
                    # 404/429 from that poll). The SDK exposes no dispatch
                    # boundary or idempotency key, so every error from this
                    # combined call is ambiguous. Re-list only the durable
                    # create attempt; never authorize a second create merely
                    # from the exception status.
                    self.invalidate_sandbox_cache(sandbox_id)
                    try:
                        sandbox = await self._find(sandbox_id, generation_token)
                    except Exception as lookup_exc:
                        await self._mark_reservation_ambiguous(sandbox_id)
                        reservation_finished = True
                        raise ProviderError(
                            "Daytona create outcome is unknown",
                            code="provider_create_outcome_unknown",
                            retryable=True,
                        ) from lookup_exc
                    if sandbox is None:
                        await self._mark_reservation_ambiguous(sandbox_id)
                        reservation_finished = True
                        raise ProviderError(
                            "Daytona create outcome is unknown",
                            code="provider_create_outcome_unknown",
                            retryable=True,
                        ) from exc
                await self._finish_reservation(
                    sandbox_id,
                    created=True,
                    provider_id=str(getattr(sandbox, "id", "")),
                )
                reservation_finished = True
                self._sandboxes[sandbox_id] = sandbox
                return await self._status(sandbox_id, sandbox)
            finally:
                if not reservation_finished:
                    await self._finish_reservation(sandbox_id, created=False)

    async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
        sandbox = await self._find(sandbox_id, validate_cached=True)
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
        cache_at_start = dict(self._sandboxes)
        async with self._capacity_condition:
            ambiguous_at_start = tuple(self._ambiguous_capacity_reservations)
        managed: list[ManagedSandbox] = []
        observed: dict[str, object] = {}
        async for sandbox in self._iter(self._labels()):
            labels = dict(getattr(sandbox, "labels", None) or {})
            sandbox_id = labels.get("agentbox-id")
            if not sandbox_id:
                continue
            provider_id = str(getattr(sandbox, "id", sandbox_id))
            observed[sandbox_id] = sandbox
            managed.append(
                ManagedSandbox(
                    ref=SandboxRef(sandbox_id, provider_id),
                    status=await self._status(sandbox_id, sandbox),
                    instance_id=self._instance_id(sandbox),
                    metadata=labels,
                )
            )
        self._commit_complete_inventory(observed, cache_at_start=cache_at_start)
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

    @staticmethod
    def _is_recoverable(sandbox) -> bool:  # type: ignore[no-untyped-def]
        if bool(getattr(sandbox, "recoverable", False)):
            return True
        error_reason = getattr(sandbox, "error_reason", None)
        return bool(getattr(error_reason, "recoverable", False))

    async def release(self, sandbox_id: str) -> bool:
        sandbox = await self._find(sandbox_id)
        if sandbox is None:
            await self._refresh_capacity_inventory()
            return False
        provider_id = str(getattr(sandbox, "id", ""))
        if self._state(sandbox) not in {
            "started",
            "creating",
            "restoring",
            "starting",
            "resuming",
        }:
            await self._record_capacity_removed(provider_id)
            return False
        try:
            await self.daytona.stop(
                sandbox,
                timeout=self.config.ready_timeout_seconds,
            )
            await self._record_capacity_removed(provider_id)
            return True
        except self._sdk.not_found_error:
            self.invalidate_sandbox_cache(sandbox_id)
            await self._refresh_capacity_inventory()
            return False
        except self._sdk.error as exc:
            raise ProviderError(
                f"Daytona sandbox release failed: {exc}", retryable=True
            ) from exc

    @staticmethod
    def _instance_id(sandbox) -> str:
        provider_id = str(getattr(sandbox, "id", "unknown"))
        updated_at = getattr(sandbox, "updated_at", None)
        return f"{provider_id}:{updated_at}" if updated_at else provider_id

    async def delete(self, sandbox_id: str) -> bool:
        sandbox = await self._find(sandbox_id)
        if sandbox is None:
            await self._refresh_capacity_inventory()
            return False
        provider_id = str(getattr(sandbox, "id", ""))
        try:
            await self.daytona.delete(sandbox)
            self.invalidate_sandbox_cache(sandbox_id)
            await self._record_capacity_removed(provider_id)
            return True
        except self._sdk.not_found_error:
            self.invalidate_sandbox_cache(sandbox_id)
            await self._record_capacity_removed(provider_id)
            # The exact provider ID is already absent; deletion converged.
            return True
        except self._sdk.error as exc:
            raise ProviderError(
                f"Daytona sandbox deletion failed: {exc}", retryable=True
            ) from exc

    async def purge_managed(self, ref: SandboxRef) -> bool:
        """Purge only the provider generation observed by reconciliation."""

        cached = self._sandboxes.get(ref.sandbox_id)
        target = (
            cached
            if cached is not None
            and str(getattr(cached, "id", "")) == ref.provider_id
            else None
        )
        if target is None:
            try:
                # Use the durable exact ID. Label inventory is eventually
                # consistent and cannot confirm deletion after a lost reply.
                target = await self.daytona.get(ref.provider_id)
            except self._sdk.not_found_error:
                cached = self._sandboxes.get(ref.sandbox_id)
                if cached is None or str(getattr(cached, "id", "")) == ref.provider_id:
                    self.invalidate_sandbox_cache(ref.sandbox_id)
                await self._record_capacity_removed(ref.provider_id)
                return True
        if str(getattr(target, "id", "")) != ref.provider_id:
            raise ProviderError(
                "Daytona exact lookup returned a different provider generation",
                code="endpoint_generation_changed",
                retryable=True,
                status_code=409,
            )
        try:
            await self.daytona.delete(target)
        except self._sdk.not_found_error:
            cached = self._sandboxes.get(ref.sandbox_id)
            if cached is None or str(getattr(cached, "id", "")) == ref.provider_id:
                self.invalidate_sandbox_cache(ref.sandbox_id)
            await self._record_capacity_removed(ref.provider_id)
            return True
        except self._sdk.error as exc:
            raise ProviderError(
                f"Daytona managed sandbox purge failed: {exc}", retryable=True
            ) from exc
        cached = self._sandboxes.get(ref.sandbox_id)
        if cached is None or str(getattr(cached, "id", "")) == ref.provider_id:
            self.invalidate_sandbox_cache(ref.sandbox_id)
        await self._record_capacity_removed(ref.provider_id)
        return True

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
        except self._sdk.not_found_error as exc:
            self.invalidate_sandbox_cache(sandbox_id)
            raise SandboxNotFoundError(sandbox_id) from exc
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
            provider_id=str(getattr(sandbox, "id", "")) or None,
        )

    async def close(self) -> None:
        self._sandboxes.clear()
        close = getattr(self.daytona, "close", None)
        if close is not None:
            await close()
