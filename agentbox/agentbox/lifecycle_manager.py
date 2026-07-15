from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import time
from urllib import error as urlerror
from urllib import request as urlrequest

from fastapi import HTTPException

from agentbox.apps import sandbox_app
from agentbox.config import settings
from agentbox.providers import SandboxProvider
from agentbox.providers.errors import ProviderError
from agentbox.providers.models import ManagedSandbox, ProviderCapacityPolicy
from agentbox.providers.protocol import (
    SandboxBootstrapProvider,
    SandboxCapacityProvider,
    SandboxManagedPurgeProvider,
)
from agentbox.schemas import SandboxEnsureRequest, SandboxInternalStatus
from agentbox.state_store.models import (
    LifecycleClaim,
    ProviderAllocation,
    SandboxRecord,
)
from agentbox.state_store.protocol import AsyncStateStore


logger = logging.getLogger(__name__)
_OUTCOME_UNKNOWN_CODES = {
    "provider_create_outcome_unknown",
    "provider_cleanup_outcome_unknown",
    "provider_observation_unknown",
}


class SandboxLifecycleManager:
    """Durable lifecycle authority layered over provider compute adapters."""

    def __init__(
        self,
        provider: SandboxProvider,
        store: AsyncStateStore,
        *,
        owner: str,
    ) -> None:
        self.provider = provider
        self.store = store
        self.owner = owner
        self._reconcile_lock = asyncio.Lock()

    @property
    def capacity_policy(self) -> ProviderCapacityPolicy | None:
        if isinstance(self.provider, SandboxCapacityProvider):
            return self.provider.capacity_policy
        return None

    @asynccontextmanager
    async def claim(self, sandbox_id: str, operation: str):
        deadline = (
            asyncio.get_running_loop().time()
            + settings.agentbox_lifecycle_claim_wait_seconds
        )
        claim: LifecycleClaim | None = None
        while claim is None and asyncio.get_running_loop().time() < deadline:
            claim = await self.store.acquire_lifecycle_claim(
                sandbox_id,
                operation=operation,
                owner=self.owner,
                ttl_seconds=settings.agentbox_lifecycle_claim_ttl_seconds,
            )
            if claim is None:
                if await self.store.get_sandbox(sandbox_id) is None:
                    raise ProviderError(
                        f"Sandbox {sandbox_id} does not exist",
                        code="sandbox_not_found",
                        status_code=404,
                    )
                await asyncio.sleep(0.05)
        if claim is None:
            raise ProviderError(
                f"Sandbox {sandbox_id} lifecycle is busy",
                code="lifecycle_busy",
                retryable=True,
                status_code=409,
                headers={"Retry-After": "1"},
            )

        owner_task = asyncio.current_task()
        renewal = asyncio.create_task(self._renew_claim(claim, owner_task))
        try:
            yield claim
        finally:
            renewal.cancel()
            try:
                await renewal
            except asyncio.CancelledError:
                pass
            await self.store.release_lifecycle_claim(
                claim.claim_id,
                owner=self.owner,
            )

    async def _renew_claim(
        self,
        claim: LifecycleClaim,
        owner_task: asyncio.Task | None,
    ) -> None:
        interval = max(settings.agentbox_lifecycle_claim_ttl_seconds / 3, 1.0)
        while True:
            await asyncio.sleep(interval)
            renewed = await self.store.renew_lifecycle_claim(
                claim.claim_id,
                owner=self.owner,
                ttl_seconds=settings.agentbox_lifecycle_claim_ttl_seconds,
            )
            if renewed is None:
                logger.error(
                    "agentbox_lifecycle_claim_lost sandbox_id=%s operation=%s",
                    claim.sandbox_id,
                    claim.operation,
                )
                # Continuing after the durable claim expires permits a second
                # manager to mutate the same sandbox. Cancellation is our
                # fencing signal; provider creates are separately shielded and
                # recorded before cancellation is re-raised.
                if owner_task is not None:
                    owner_task.cancel()
                return

    async def ensure(
        self,
        sandbox_id: str,
        request: SandboxEnsureRequest,
    ) -> SandboxInternalStatus:
        runtime_request = SandboxEnsureRequest(
            env={**request.env, **settings.agentbox_static_runtime_env}
        )
        previous = await self.store.get_sandbox(sandbox_id)
        record = await self.store.upsert_sandbox(sandbox_id, runtime_request)
        async with self.claim(sandbox_id, "ensure"):
            return await self._ensure_claimed(sandbox_id, previous, record)

    async def resume_claimed(self, sandbox_id: str) -> SandboxInternalStatus:
        """Resume a retained sandbox while the caller holds its lifecycle claim."""

        previous = await self.store.get_sandbox(sandbox_id)
        if previous is None or previous.desired_state == "deleted":
            raise ProviderError(
                f"Sandbox {sandbox_id} does not exist",
                code="sandbox_not_found",
                status_code=404,
            )
        runtime_request = SandboxEnsureRequest(
            env={**previous.env, **settings.agentbox_static_runtime_env}
        )
        record = await self.store.upsert_sandbox(sandbox_id, runtime_request)
        return await self._ensure_claimed(sandbox_id, previous, record)

    async def _ensure_claimed(
        self,
        sandbox_id: str,
        previous: SandboxRecord | None,
        record: SandboxRecord,
    ) -> SandboxInternalStatus:
        durable_request = record.to_ensure_request()
        # A durable env generation is provider/template state. When it changes,
        # rebuild compute behind the same logical sandbox ID.
        env_changed = previous is not None and previous.env != record.env
        if env_changed:
            await self._purge_for_env_change(sandbox_id, previous.provider_id)

        allocation = await self._reserve_allocation(sandbox_id)
        create_task = asyncio.create_task(
            self.provider.create(sandbox_id, durable_request)
        )
        cancelled: asyncio.CancelledError | None = None
        try:
            try:
                status = await asyncio.shield(create_task)
            except asyncio.CancelledError as exc:
                cancelled = exc
                # Provider create is shielded so an ACA revision shutdown cannot
                # leave accepted compute unrecorded.
                status = await create_task

            if isinstance(self.provider, SandboxBootstrapProvider):
                await self.provider.bootstrap(sandbox_id, durable_request)
            endpoint = await self._wait_until_runtime_ready(sandbox_id)
            managed = await self._managed_sandbox(sandbox_id)
            if managed is None:
                raise ProviderError(
                    "Provider instance identity is unavailable after create",
                    code="provider_observation_unknown",
                    retryable=True,
                )
            provider_id = managed.ref.provider_id
            instance_id = endpoint.instance_id or managed.instance_id
            observed = await self.store.set_sandbox_observation(
                sandbox_id,
                provider_name=self.provider.provider_name,
                provider_id=provider_id,
                instance_id=instance_id,
                observed_generation=record.desired_generation,
            )
            if observed is None:
                raise ProviderError(
                    "Sandbox desired generation changed during create",
                    code="provider_observation_unknown",
                    retryable=True,
                    status_code=409,
                )
            await self._activate_allocation(
                sandbox_id,
                provider_id,
                allocation=allocation,
            )
            if cancelled is not None:
                raise cancelled
            return status
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError) and cancelled is not None:
                raise
            await self._handle_failed_ensure(sandbox_id, allocation, exc)
            raise

    async def _purge_for_env_change(
        self, sandbox_id: str, provider_id: str | None
    ) -> None:
        try:
            await self.provider.delete(sandbox_id)
        except (ProviderError, HTTPException) as exc:
            if self._is_not_found(exc):
                pass
            else:
                raise
        await self._release_sandbox_allocations(
            sandbox_id,
            provider_id=provider_id,
            include_reservations=True,
        )

    async def _reserve_allocation(self, sandbox_id: str):
        policy = self.capacity_policy
        if policy is None:
            return None
        allocation = await self.store.reserve_provider_allocation(
            policy.scope,
            sandbox_id,
            owner=self.owner,
            max_active=policy.max_active,
            ttl_seconds=settings.agentbox_provider_allocation_ttl_seconds,
        )
        if allocation is None:
            raise ProviderError(
                f"Provider concurrency limit ({policy.max_active}) reached",
                code="capacity_exhausted",
                retryable=True,
                status_code=429,
                headers={"Retry-After": "15"},
            )
        return allocation

    async def _activate_allocation(
        self,
        sandbox_id: str,
        provider_id: str,
        *,
        allocation: ProviderAllocation | None,
    ) -> None:
        policy = self.capacity_policy
        if policy is None:
            return
        if allocation is None:
            raise ProviderError(
                "Provider allocation is missing after create",
                code="provider_observation_unknown",
                retryable=True,
            )
        allocation = await self.store.activate_provider_allocation(
            policy.scope,
            allocation.allocation_id,
            owner=self.owner,
            provider_id=provider_id,
        )
        if allocation is None:
            adopted = next(
                (
                    row
                    for row in await self.store.list_provider_allocations(policy.scope)
                    if row.sandbox_id == sandbox_id
                    and row.state == "active"
                    and row.provider_id == provider_id
                ),
                None,
            )
            if adopted is not None:
                return
            raise ProviderError(
                "Provider allocation could not be activated",
                code="provider_observation_unknown",
                retryable=True,
            )

    async def _release_allocation(self, allocation_id: str) -> None:
        policy = self.capacity_policy
        if policy is not None:
            await self.store.release_provider_allocation(policy.scope, allocation_id)

    async def _release_sandbox_allocations(
        self,
        sandbox_id: str,
        *,
        provider_id: str | None = None,
        include_reservations: bool = False,
    ) -> None:
        policy = self.capacity_policy
        if policy is None:
            return
        for allocation in await self.store.list_provider_allocations(policy.scope):
            if allocation.sandbox_id != sandbox_id:
                continue
            if allocation.state == "reserved" and not include_reservations:
                continue
            if (
                allocation.state == "active"
                and provider_id is not None
                and allocation.provider_id != provider_id
            ):
                continue
            await self._release_allocation(allocation.allocation_id)

    async def _handle_failed_ensure(
        self,
        sandbox_id: str,
        allocation: ProviderAllocation | None,
        exc: BaseException,
    ) -> None:
        if isinstance(exc, ProviderError) and exc.code in _OUTCOME_UNKNOWN_CODES:
            # Keep the durable slot until reconciliation proves the provider
            # object absent or adopts it as active.
            return
        try:
            await self._release_compute_if_present(sandbox_id)
        except Exception:
            logger.exception(
                "agentbox_failed_ensure_cleanup_unknown sandbox_id=%s",
                sandbox_id,
            )
            return
        if allocation is not None:
            await self._release_allocation(allocation.allocation_id)

    async def _release_compute_if_present(self, sandbox_id: str) -> None:
        from agentbox.api.lifecycle import release_sandbox_compute

        try:
            await release_sandbox_compute(self.provider, sandbox_id)
        except (ProviderError, HTTPException) as exc:
            if not self._is_not_found(exc):
                raise

    async def suspend(self, sandbox_id: str) -> bool:
        record = await self.store.get_sandbox(sandbox_id)
        if record is None:
            return False
        async with self.claim(sandbox_id, "suspend"):
            return await self._suspend_claimed(record)

    async def suspend_if_idle(self, sandbox_id: str) -> bool:
        """Fence new activity, recheck idleness, then release compute."""

        async with self.claim(sandbox_id, "idle-suspend"):
            candidates = {
                record.sandbox_id: record
                for record in await self.store.idle_sandboxes(
                    settings.agentbox_sandbox_idle_timeout_seconds
                )
            }
            record = candidates.get(sandbox_id)
            if record is None or record.desired_state != "present":
                return False
            return await self._suspend_claimed(record)

    async def _suspend_claimed(self, record) -> bool:
        from agentbox.api.lifecycle import release_sandbox_compute

        released = await release_sandbox_compute(
            self.provider,
            record.sandbox_id,
        )
        await self._release_sandbox_allocations(
            record.sandbox_id,
            provider_id=record.provider_id,
            include_reservations=True,
        )
        await self.store.delete_sandbox_sessions(record.sandbox_id)
        await self.store.mark_pod_stopped(record.sandbox_id)
        return released

    async def delete_session_if_idle(
        self, sandbox_id: str, session_id: str
    ) -> bool:
        async with self.claim(sandbox_id, "idle-session-delete"):
            candidates = {
                (record.sandbox_id, record.session_id)
                for record in await self.store.expired_sessions(
                    settings.agentbox_session_idle_timeout_seconds
                )
            }
            if (sandbox_id, session_id) not in candidates:
                return False
            from agentbox.api.lifecycle import delete_runtime_session_if_present

            deleted = await delete_runtime_session_if_present(
                self.provider,
                sandbox_id,
                session_id,
            )
            return await self.store.delete_session(sandbox_id, session_id) or deleted

    async def heartbeat_sandbox(self, sandbox_id: str) -> bool:
        record = await self.store.get_sandbox(sandbox_id)
        if record is None or record.desired_state != "present":
            return False
        async with self.claim(sandbox_id, "sandbox-heartbeat"):
            return await self.store.mark_sandbox_active(
                sandbox_id,
                owner=self.owner,
            )

    async def heartbeat_session(self, sandbox_id: str, session_id: str) -> bool:
        record = await self.store.get_sandbox(sandbox_id)
        session = await self.store.get_session(sandbox_id, session_id)
        if (
            record is None
            or record.desired_state != "present"
            or session is None
        ):
            return False
        async with self.claim(sandbox_id, "session-heartbeat"):
            return await self.store.touch_session(
                sandbox_id,
                session_id,
                owner=self.owner,
            )

    async def delete(self, sandbox_id: str) -> bool:
        record = await self.store.get_sandbox(sandbox_id)
        if record is None:
            try:
                deleted = await self.provider.delete(sandbox_id)
            except (ProviderError, HTTPException) as exc:
                if self._is_not_found(exc):
                    deleted = False
                else:
                    raise
            return await self._purge_remaining_managed(sandbox_id) or deleted
        async with self.claim(sandbox_id, "delete"):
            await self.store.set_sandbox_desired_state(sandbox_id, "deleted")
            try:
                deleted = await self.provider.delete(sandbox_id)
            except (ProviderError, HTTPException) as exc:
                if self._is_not_found(exc):
                    deleted = False
                else:
                    raise
            deleted = await self._purge_remaining_managed(sandbox_id) or deleted
            await self._release_sandbox_allocations(
                sandbox_id,
                include_reservations=True,
            )
            await self.store.delete_sandbox(sandbox_id)
            return deleted

    async def _purge_remaining_managed(self, sandbox_id: str) -> bool:
        if not isinstance(self.provider, SandboxManagedPurgeProvider):
            return False
        deleted = False
        for item in await self.provider.list_managed():
            if item.ref.sandbox_id == sandbox_id:
                deleted = await self.provider.purge_managed(item.ref) or deleted
        return deleted

    async def _wait_until_runtime_ready(self, sandbox_id: str):
        deadline = (
            asyncio.get_running_loop().time()
            + settings.agentbox_sandbox_ready_timeout_seconds
        )
        last_error: BaseException | None = None
        runtime = sandbox_app("runtime")
        while asyncio.get_running_loop().time() < deadline:
            try:
                endpoint = await self.provider.resolve_endpoint(
                    sandbox_id,
                    runtime,
                )
                request = urlrequest.Request(
                    f"{endpoint.base_url.rstrip('/')}{runtime.health_path}",
                    headers=dict(endpoint.headers),
                    method="GET",
                )

                def check() -> bool:
                    with urlrequest.urlopen(request, timeout=2) as response:
                        return 200 <= response.status < 300

                if await asyncio.to_thread(check):
                    return endpoint
            except (
                ProviderError,
                HTTPException,
                urlerror.URLError,
                OSError,
                TimeoutError,
            ) as exc:
                last_error = exc
            await asyncio.sleep(0.25)
        raise ProviderError(
            "Sandbox runtime did not pass its health check",
            code="runtime_not_ready",
            retryable=True,
            status_code=504,
        ) from last_error

    async def _managed_sandbox(self, sandbox_id: str) -> ManagedSandbox | None:
        try:
            inventory = await self.provider.list_managed()
        except Exception as exc:
            raise ProviderError(
                "Provider inventory is unavailable after create",
                code="provider_observation_unknown",
                retryable=True,
            ) from exc
        return next(
            (item for item in inventory if item.ref.sandbox_id == sandbox_id),
            None,
        )

    async def reconcile(self) -> None:
        if self._reconcile_lock.locked():
            return
        async with self._reconcile_lock:
            inventory_started_at = time.time()
            inventory = await self.provider.list_managed()
            inventory_by_sandbox = {
                item.ref.sandbox_id: item for item in inventory
            }
            policy = self.capacity_policy
            if policy is not None:
                active = {
                    item.ref.provider_id: item.ref.sandbox_id
                    for item in inventory
                    if item.status.status in {"CREATING", "RUNNING"}
                }
                await self.store.reconcile_provider_allocations(
                    policy.scope,
                    active,
                    inventory_started_at=inventory_started_at,
                )

            records = {
                record.sandbox_id: record
                for record in await self.store.list_sandboxes()
            }
            known_provider_ids = {
                record.provider_id
                for record in records.values()
                if record.provider_id
            }
            for item in inventory:
                if item.ref.provider_id in known_provider_ids:
                    await self.store.clear_orphan(
                        self.provider.provider_name,
                        item.ref.provider_id,
                    )
                else:
                    await self.store.observe_orphan(
                        self.provider.provider_name,
                        item.ref.provider_id,
                        sandbox_id=item.ref.sandbox_id,
                        observed_at=inventory_started_at,
                    )

            now = time.time()
            idle_without_compute = {
                record.sandbox_id
                for record in await self.store.idle_sandboxes(
                    settings.agentbox_sandbox_idle_timeout_seconds
                )
            }
            for record in records.values():
                item = inventory_by_sandbox.get(record.sandbox_id)
                if record.desired_state == "deleted":
                    await self.delete(record.sandbox_id)
                elif record.desired_state == "suspended":
                    if (
                        record.idle_since_at is not None
                        and now - record.idle_since_at
                        >= settings.agentbox_suspended_retention_seconds
                    ):
                        await self.delete(record.sandbox_id)
                    elif item is not None and item.status.ready:
                        await self.suspend(record.sandbox_id)
                elif item is None and record.sandbox_id in idle_without_compute:
                    await self.store.mark_pod_stopped(record.sandbox_id)
                elif (
                    item is None
                    or record.provider_id != item.ref.provider_id
                    or record.observed_generation != record.desired_generation
                ):
                    await self.ensure(record.sandbox_id, record.to_ensure_request())

            expired = await self.store.expired_orphans(
                settings.agentbox_orphan_grace_seconds,
                inventory_started_at=inventory_started_at,
            )
            if isinstance(self.provider, SandboxManagedPurgeProvider):
                fresh_inventory = {
                    item.ref.provider_id: item
                    for item in await self.provider.list_managed()
                }
                for orphan in expired:
                    item = fresh_inventory.get(orphan.provider_id)
                    if item is None:
                        await self.store.clear_orphan(
                            orphan.provider_name,
                            orphan.provider_id,
                        )
                        continue
                    await self.provider.purge_managed(item.ref)
                    if policy is not None:
                        for allocation in await self.store.list_provider_allocations(
                            policy.scope
                        ):
                            if allocation.provider_id == item.ref.provider_id:
                                await self.store.release_provider_allocation(
                                    policy.scope,
                                    allocation.allocation_id,
                                )
                    await self.store.clear_orphan(
                        orphan.provider_name,
                        orphan.provider_id,
                    )

    @staticmethod
    def _is_not_found(exc: BaseException) -> bool:
        return bool(
            isinstance(exc, ProviderError) and exc.status_code == 404
            or isinstance(exc, HTTPException) and exc.status_code == 404
        )


async def reconciliation_loop(manager: SandboxLifecycleManager) -> None:
    while True:
        await asyncio.sleep(settings.agentbox_reconcile_interval_seconds)
        try:
            await manager.reconcile()
        except Exception:
            logger.exception("AgentBox reconciliation pass failed")
