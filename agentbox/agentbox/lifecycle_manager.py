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
from agentbox.endpoint_transport import (
    EndpointRoutingUnavailable,
    request_endpoint_http,
)
from agentbox.providers import SandboxProvider
from agentbox.providers.errors import ProviderError
from agentbox.providers.models import (
    ManagedSandbox,
    ProviderCapacityPolicy,
    SandboxRef,
)
from agentbox.providers.protocol import (
    SandboxAdoptionProvider,
    SandboxBootstrapProvider,
    SandboxCapacityProvider,
    SandboxGenerationCreateProvider,
    SandboxLeaseProvider,
    SandboxManagedPurgeProvider,
    SandboxStoragePurgeProvider,
)
from agentbox.schemas import SandboxEnsureRequest, SandboxInternalStatus
from agentbox.state_store.models import (
    LifecycleClaim,
    OrphanCandidate,
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
_OUTCOME_UNKNOWN_ALLOCATION_OWNER = "agentbox:outcome-unknown"


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
                    if operation == "ensure":
                        # A serialized DELETE may remove its temporary
                        # tombstone while this ENSURE is waiting. Recreate the
                        # empty durable row, then compete for the next claim.
                        await self.store.insert_sandbox_if_missing(sandbox_id)
                        continue
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
        # Lifecycle claims are foreign-keyed to the sandbox row. Create only
        # defaults before claiming; the desired-state upsert itself remains
        # inside the claim below.
        await self.store.insert_sandbox_if_missing(sandbox_id)
        async with self.claim(sandbox_id, "ensure"):
            # Desired state must change under the same lifecycle fence as the
            # provider mutation. Otherwise a concurrent absent-record DELETE
            # can purge compute or storage created by this ENSURE.
            previous = await self.store.get_sandbox(sandbox_id)
            if (
                previous is not None
                and previous.desired_state == "present"
                and previous.provider_id is None
                and previous.observed_generation == 0
                and previous.env == {}
            ):
                # insert_sandbox_if_missing creates a claim anchor, not a
                # provider generation. Don't treat its empty environment as a
                # durable-env change that requires deleting compute.
                previous = None
            if (
                previous is not None
                and previous.env != runtime_request.env
                and previous.provider_id is None
                and await self._has_provider_allocation(previous.sandbox_id)
            ):
                raise ProviderError(
                    "Sandbox create outcome must be resolved before changing environment",
                    code="provider_create_outcome_unknown",
                    retryable=True,
                    status_code=503,
                    headers={"Retry-After": "1"},
                )
            if previous is not None and previous.env != runtime_request.env:
                if previous.provider_id is not None:
                    await self._purge_for_env_change(
                        sandbox_id,
                        previous.provider_id,
                    )
                    cleared = await self.store.clear_sandbox_provider_identity(
                        sandbox_id,
                        provider_id=previous.provider_id,
                        desired_generation=previous.desired_generation,
                    )
                    if cleared is None:
                        raise ProviderError(
                            "Sandbox changed while replacing its environment",
                            code="provider_observation_unknown",
                            retryable=True,
                            status_code=409,
                        )
                # Publish the new environment only after exact deletion is
                # confirmed. A failed purge leaves the old request intact.
                previous = None
            record = await self.store.upsert_sandbox(sandbox_id, runtime_request)
            return await self._ensure_claimed(sandbox_id, previous, record)

    async def _has_provider_allocation(self, sandbox_id: str) -> bool:
        policy = self.capacity_policy
        return bool(
            policy is not None
            and any(
                row.sandbox_id == sandbox_id
                for row in await self.store.list_provider_allocations(policy.scope)
            )
        )

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
        if previous is not None and previous.provider_name not in {
            None,
            self.provider.provider_name,
        }:
            raise ProviderError(
                "Sandbox belongs to a different provider; explicit migration is required",
                code="provider_mismatch",
                status_code=409,
            )

        status: SandboxInternalStatus | None = None
        allocation: ProviderAllocation | None = None
        if env_changed:
            raise RuntimeError("environment replacement was not fenced before ensure")
        if (
            previous is not None
            and previous.provider_id is not None
            and previous.provider_name in {None, self.provider.provider_name}
            and isinstance(self.provider, SandboxAdoptionProvider)
        ):
            # Adoption may resume paused compute. Reserve distributed capacity
            # before touching the provider, not after it is already running.
            allocation = await self._reserve_allocation(sandbox_id)
            policy = self.capacity_policy
            if policy is not None:
                conflicting = [
                    row
                    for row in await self.store.list_provider_allocations(policy.scope)
                    if row.sandbox_id == sandbox_id
                    and row.state == "active"
                    and row.provider_id != previous.provider_id
                ]
                if conflicting:
                    raise ProviderError(
                        "Another provider generation is still allocated",
                        code="provider_generation_conflict",
                        retryable=True,
                        status_code=409,
                    )
            await self.provider.adopt(
                sandbox_id,
                previous.provider_id,
            )
            status = await self.provider.get_status(sandbox_id)
        elif previous is not None and previous.provider_id is not None:
            managed = await self._managed_sandbox(sandbox_id)
            if managed is not None and managed.ref.provider_id == previous.provider_id:
                current = await self.provider.get_status(sandbox_id)
                if current.ready:
                    status = current
            else:
                raise ProviderError(
                    "Provider cannot verify the durable sandbox generation",
                    code="provider_adoption_unavailable",
                    retryable=True,
                    status_code=503,
                )

        if status is None and record.provider_id is None:
            recovered = await self._recover_outcome_unknown_generation(
                sandbox_id,
                record,
            )
            if recovered is not None:
                status, allocation, record = recovered

        if (
            status is not None
            and status.ready
            and record.provider_id is not None
            and record.observed_generation == record.desired_generation
        ):
            # This exact durable generation already passed bootstrap and both
            # runtime readiness gates. A repeated ensure (for example session
            # creation immediately after PUT /sandboxes) must not restart or
            # re-bootstrap it through an eventually inconsistent provider
            # control plane. Individual app requests still perform exact-ID
            # binding and surface retryable routing errors.
            await self._activate_allocation(
                sandbox_id,
                record.provider_id,
                allocation=allocation,
            )
            return status

        if status is None and record.provider_id is None and allocation is None:
            durable_orphans = await self.store.list_orphans(
                self.provider.provider_name,
                sandbox_id=sandbox_id,
            )
            if durable_orphans:
                # A previously observed provider generation remains an exact
                # durable fence even when the current eventual inventory omits
                # it. Only explicit exact purge may clear this condition.
                raise ProviderError(
                    "A previously observed provider generation is unresolved",
                    code="provider_generation_conflict",
                    retryable=True,
                    status_code=409,
                )
            existing_generations = [
                item
                for item in await self.provider.list_managed()
                if item.ref.sandbox_id == sandbox_id
            ]
            if existing_generations:
                raise ProviderError(
                    "Untracked provider generation already exists for sandbox",
                    code="provider_generation_conflict",
                    retryable=True,
                    status_code=409,
                )

        if allocation is None:
            allocation = await self._reserve_allocation(sandbox_id)
        policy = self.capacity_policy
        if status is None and policy is not None and record.provider_id is None:
            if allocation is None:
                raise ProviderError(
                    "Provider allocation is missing before create",
                    code="provider_observation_unknown",
                    retryable=True,
                )
            if allocation.state != "reserved":
                # An active allocation is proof that a provider generation
                # may already exist. _recover_outcome_unknown_generation()
                # must adopt that exact generation before create is allowed.
                raise ProviderError(
                    "Existing provider generation has not been adopted",
                    code="provider_create_outcome_unknown",
                    retryable=True,
                    status_code=503,
                    headers={"Retry-After": "1"},
                )
            allocation = await self.store.hold_provider_allocation(
                policy.scope,
                allocation.allocation_id,
                owner=_OUTCOME_UNKNOWN_ALLOCATION_OWNER,
            )
            if allocation is None:
                raise ProviderError(
                    "Provider create intent could not be durably fenced",
                    code="provider_observation_unknown",
                    retryable=True,
                )
        create_task = None
        generation_create_started = False
        if status is None:
            if policy is not None and record.provider_id is None:
                if not isinstance(self.provider, SandboxGenerationCreateProvider):
                    raise ProviderError(
                        "Cloud provider cannot bind create to a durable generation",
                        code="provider_observation_unknown",
                        retryable=True,
                    )
                assert allocation is not None
                # The allocation is already held with a non-expiring owner.
                # From this point onward *any* failure is ambiguous unless an
                # exact purge succeeds. Never release this fence merely because
                # a later state-store write or bootstrap step failed.
                generation_create_started = True
                create_task = asyncio.create_task(
                    self.provider.create_generation(
                        sandbox_id,
                        durable_request,
                        generation_token=allocation.allocation_id,
                    )
                )
            else:
                create_task = asyncio.create_task(
                    self.provider.create(sandbox_id, durable_request)
                )
        cancelled: asyncio.CancelledError | None = None
        try:
            if create_task is not None:
                try:
                    status = await asyncio.shield(create_task)
                except asyncio.CancelledError as exc:
                    cancelled = exc
                    # Provider create is shielded so an ACA revision shutdown cannot
                    # leave accepted compute unrecorded.
                    status = await create_task
            assert status is not None

            runtime = sandbox_app("runtime")
            try:
                endpoint = await self.provider.resolve_endpoint(
                    sandbox_id,
                    runtime,
                )
            except BaseException as exc:
                raise ProviderError(
                    "Provider identity is unavailable after create or adoption",
                    code="provider_observation_unknown",
                    retryable=True,
                ) from exc
            provider_id = endpoint.provider_id
            identity_instance_id = endpoint.instance_id
            if provider_id is None:
                # Docker/Kubernetes expose their exact container/pod identity
                # through inventory rather than the routed endpoint. An empty
                # or conflicting inventory still fails closed; it never
                # authorizes another create.
                identity_managed = await self._managed_sandbox(sandbox_id)
                if identity_managed is not None:
                    provider_id = identity_managed.ref.provider_id
                    identity_instance_id = identity_managed.instance_id
            if provider_id is None:
                raise ProviderError(
                    "Provider endpoint did not expose an exact generation",
                    code="provider_observation_unknown",
                    retryable=True,
                )
            identity = await self.store.set_sandbox_provider_identity(
                sandbox_id,
                provider_name=self.provider.provider_name,
                provider_id=provider_id,
                instance_id=identity_instance_id,
                desired_generation=record.desired_generation,
            )
            if identity is None:
                raise ProviderError(
                    "Sandbox provider identity conflicted with durable state",
                    code="endpoint_generation_changed",
                    retryable=True,
                    status_code=409,
                )
            await self._activate_allocation(
                sandbox_id,
                provider_id,
                allocation=allocation,
            )

            if isinstance(self.provider, SandboxBootstrapProvider):
                await self.provider.bootstrap(sandbox_id, durable_request)
            endpoint = await self._wait_until_runtime_ready(sandbox_id)
            managed = await self._managed_sandbox(sandbox_id)
            if managed is not None and managed.ref.provider_id != provider_id:
                raise ProviderError(
                    "Provider inventory and endpoint generations disagree",
                    code="endpoint_generation_changed",
                    retryable=True,
                    status_code=409,
                )
            instance_id = endpoint.instance_id or (
                managed.instance_id if managed is not None else None
            )
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
            if cancelled is not None:
                raise cancelled
            return status
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError) and cancelled is not None:
                raise
            await self._handle_failed_ensure(
                sandbox_id,
                allocation,
                exc,
                generation_create_started=generation_create_started,
            )
            raise

    async def _purge_for_env_change(
        self, sandbox_id: str, provider_id: str | None
    ) -> None:
        try:
            if provider_id is not None and isinstance(
                self.provider, SandboxManagedPurgeProvider
            ):
                await self._purge_cloud_generations(
                    sandbox_id,
                    record_provider_id=provider_id,
                )
            else:
                await self.provider.delete(sandbox_id)
        except (ProviderError, HTTPException) as exc:
            if self._is_not_found(exc):
                pass
            else:
                raise
        await self._release_sandbox_allocations(
            sandbox_id,
            include_reservations=True,
        )

    async def _recover_outcome_unknown_generation(
        self,
        sandbox_id: str,
        record: SandboxRecord,
    ) -> tuple[SandboxInternalStatus, ProviderAllocation, SandboxRecord] | None:
        policy = self.capacity_policy
        if policy is None:
            return None
        all_allocations = [
            row
            for row in await self.store.list_provider_allocations(policy.scope)
            if row.sandbox_id == sandbox_id
        ]
        discoveries = [
            row
            for row in all_allocations
            if row.allocation_id.startswith("provider:")
        ]
        if discoveries:
            raise ProviderError(
                "Another provider generation is still allocated",
                code="provider_generation_conflict",
                retryable=True,
                status_code=409,
            )
        allocations = [
            row
            for row in all_allocations
            if not row.allocation_id.startswith("provider:")
        ]
        if not allocations:
            return None
        if len(allocations) != 1:
            raise ProviderError(
                "Multiple provider generations exist for one logical sandbox",
                code="provider_generation_conflict",
                retryable=True,
                status_code=409,
            )
        allocation = allocations[0]

        if allocation.state == "active" and allocation.provider_id is not None:
            if not isinstance(self.provider, SandboxAdoptionProvider):
                raise ProviderError(
                    "Provider cannot adopt the durable allocated generation",
                    code="provider_adoption_unavailable",
                    retryable=True,
                    status_code=503,
                )
            # Reconnect the exact generation recorded by distributed capacity
            # reconciliation. Never consult a logical-ID list to select a
            # replacement when an exact provider ID is already durable.
            await self.provider.adopt(sandbox_id, allocation.provider_id)
            identity = await self.store.set_sandbox_provider_identity(
                sandbox_id,
                provider_name=self.provider.provider_name,
                provider_id=allocation.provider_id,
                instance_id=None,
                desired_generation=record.desired_generation,
            )
            if identity is None:
                raise ProviderError(
                    "Allocated provider generation conflicted with durable state",
                    code="endpoint_generation_changed",
                    retryable=True,
                    status_code=409,
                )
            return await self.provider.get_status(sandbox_id), allocation, identity

        if allocation.state != "reserved":
            raise ProviderError(
                "Provider allocation has no exact generation identity",
                code="provider_create_outcome_unknown",
                retryable=True,
                status_code=503,
                headers={"Retry-After": "1"},
            )

        matches = [
            item
            for item in await self.provider.list_managed()
            if item.ref.sandbox_id == sandbox_id
            and item.metadata.get("agentbox-generation") == allocation.allocation_id
        ]
        if not matches:
            raise ProviderError(
                "Provider create outcome remains unknown; replacement is fenced",
                code="provider_create_outcome_unknown",
                retryable=True,
                status_code=503,
                headers={"Retry-After": "1"},
            )
        if len(matches) != 1:
            raise ProviderError(
                "Multiple provider generations exist for one logical sandbox",
                code="provider_generation_conflict",
                retryable=True,
                status_code=409,
            )
        managed = matches[0]
        if not isinstance(self.provider, SandboxAdoptionProvider):
            raise ProviderError(
                "Provider cannot adopt an ambiguously created generation",
                code="provider_adoption_unavailable",
                retryable=True,
                status_code=503,
            )
        await self.provider.adopt(sandbox_id, managed.ref.provider_id)
        identity = await self.store.set_sandbox_provider_identity(
            sandbox_id,
            provider_name=self.provider.provider_name,
            provider_id=managed.ref.provider_id,
            instance_id=managed.instance_id,
            desired_generation=record.desired_generation,
        )
        if identity is None:
            raise ProviderError(
                "Ambiguous provider generation conflicted with durable state",
                code="endpoint_generation_changed",
                retryable=True,
                status_code=409,
            )
        activated = await self.store.activate_provider_allocation(
            policy.scope,
            allocation.allocation_id,
            owner=allocation.owner,
            provider_id=managed.ref.provider_id,
        )
        if activated is None:
            raise ProviderError(
                "Ambiguous provider allocation could not be adopted",
                code="provider_observation_unknown",
                retryable=True,
            )
        return await self.provider.get_status(sandbox_id), activated, identity

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
            owner=allocation.owner,
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
        *,
        generation_create_started: bool = False,
    ) -> None:
        if generation_create_started:
            if (
                isinstance(exc, ProviderError)
                and exc.code == "provider_create_rejected"
                and allocation is not None
            ):
                # Providers use this code only for a definitive rejection
                # before remote acceptance; no generation can exist.
                await self._release_allocation(allocation.allocation_id)
                return
            # The create intent was durably held before the provider call.
            # Preserve it for every post-dispatch failure, including database
            # outages and cancellation. Recovery may adopt only this attempt's
            # token; explicit exact purge is the only operation that frees it.
            return
        if isinstance(exc, ProviderError) and exc.code in _OUTCOME_UNKNOWN_CODES:
            # Keep a non-expiring durable slot until inventory adopts the
            # provider object or explicit DELETE clears the fence. A manager
            # restart must never turn an ambiguous accepted create into a
            # second provider generation.
            policy = self.capacity_policy
            if policy is not None and allocation is not None:
                await self.store.hold_provider_allocation(
                    policy.scope,
                    allocation.allocation_id,
                    owner=_OUTCOME_UNKNOWN_ALLOCATION_OWNER,
                )
            return
        record = await self.store.get_sandbox(sandbox_id)
        if record is not None and record.provider_id is not None:
            # Provider identity was persisted before bootstrap/readiness. Keep
            # that exact generation and active allocation for the next retry.
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
        if await self.store.get_sandbox(sandbox_id) is None:
            return False
        async with self.claim(sandbox_id, "suspend"):
            record = await self.store.get_sandbox(sandbox_id)
            if record is None or record.provider_id is None:
                return False
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

        if record.provider_id is not None and isinstance(
            self.provider, SandboxAdoptionProvider
        ):
            # Logical IDs may have stale or duplicate provider generations in
            # inventory. Bind release to the durable exact generation first.
            await self.provider.adopt(record.sandbox_id, record.provider_id)
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

    async def delete_session(self, sandbox_id: str, session_id: str) -> bool:
        async with self.claim(sandbox_id, "session-delete"):
            session = await self.store.get_session(sandbox_id, session_id)
            if session is None:
                return False
            if (
                session.active_operations > 0
                or await self.store.has_active_activity_lease(
                    sandbox_id,
                    session_id=session_id,
                )
            ):
                raise ProviderError(
                    "Runtime session has active operations",
                    code="session_busy",
                    retryable=True,
                    status_code=409,
                    headers={"Retry-After": "1"},
                )
            from agentbox.api.lifecycle import delete_runtime_session_if_present

            deleted = await delete_runtime_session_if_present(
                self.provider,
                sandbox_id,
                session_id,
            )
            return await self.store.delete_session(sandbox_id, session_id) or deleted

    async def delete_session_if_idle(self, sandbox_id: str, session_id: str) -> bool:
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
            active = await self.store.mark_sandbox_active(
                sandbox_id,
                owner=self.owner,
            )
            if active:
                await self.renew_provider_lease(sandbox_id)
            return active

    async def heartbeat_session(self, sandbox_id: str, session_id: str) -> bool:
        record = await self.store.get_sandbox(sandbox_id)
        session = await self.store.get_session(sandbox_id, session_id)
        if record is None or record.desired_state != "present" or session is None:
            return False
        async with self.claim(sandbox_id, "session-heartbeat"):
            active = await self.store.touch_session(
                sandbox_id,
                session_id,
                owner=self.owner,
            )
            if active:
                await self.renew_provider_lease(sandbox_id)
            return active

    async def renew_provider_lease(self, sandbox_id: str) -> None:
        """Extend provider compute lifetime when the adapter exposes the hook."""

        if isinstance(self.provider, SandboxLeaseProvider):
            await self.provider.renew_lease(sandbox_id)

    async def bind_exact_generation_claimed(self, record: SandboxRecord) -> None:
        """Bind provider calls to durable identity while lifecycle is claimed."""

        if record.provider_id is None:
            return
        if record.provider_name not in {None, self.provider.provider_name}:
            raise ProviderError(
                "Sandbox belongs to a different provider",
                code="provider_mismatch",
                status_code=409,
            )
        if not isinstance(self.provider, SandboxAdoptionProvider):
            return
        # After manager restart, process-local provider caches are empty and
        # eventually consistent inventory may omit the sandbox. Reconnect only
        # the exact durable provider ID before any session/app request.
        await self.provider.adopt(record.sandbox_id, record.provider_id)

    async def delete(self, sandbox_id: str) -> bool:
        # Always fence DELETE, including repeated deletes without a durable
        # row. A temporary tombstone prevents a concurrent ENSURE from
        # publishing a new generation while provider compute and workspace
        # storage are being purged.
        await self.store.insert_sandbox_if_missing(sandbox_id)
        async with self.claim(sandbox_id, "delete"):
            record = await self.store.get_sandbox(sandbox_id)
            if record is None:
                await self.store.insert_sandbox_if_missing(sandbox_id)
            await self.store.set_sandbox_desired_state(sandbox_id, "deleted")
            if isinstance(
                self.provider, SandboxGenerationCreateProvider
            ) and isinstance(self.provider, SandboxManagedPurgeProvider):
                deleted = await self._purge_cloud_generations_for_delete(
                    sandbox_id,
                    record,
                )
            else:
                try:
                    deleted = await self.provider.delete(sandbox_id)
                except (ProviderError, HTTPException) as exc:
                    if self._is_not_found(exc):
                        deleted = False
                    else:
                        raise
                deleted = await self._purge_remaining_managed(sandbox_id) or deleted
            deleted = await self._purge_storage(sandbox_id) or deleted
            await self._release_sandbox_allocations(
                sandbox_id,
                include_reservations=True,
            )
            await self.store.delete_sandbox(sandbox_id)
            return deleted

    async def _purge_cloud_generations_for_delete(
        self,
        sandbox_id: str,
        record: SandboxRecord | None,
    ) -> bool:
        return await self._purge_cloud_generations(
            sandbox_id,
            record_provider_id=record.provider_id if record is not None else None,
        )

    async def _purge_cloud_generations(
        self,
        sandbox_id: str,
        *,
        record_provider_id: str | None,
    ) -> bool:
        assert isinstance(self.provider, SandboxManagedPurgeProvider)
        policy = self.capacity_policy
        allocations = (
            [
                row
                for row in await self.store.list_provider_allocations(policy.scope)
                if row.sandbox_id == sandbox_id
            ]
            if policy is not None
            else []
        )
        refs: dict[str, SandboxRef] = {}
        if record_provider_id is not None:
            refs[record_provider_id] = SandboxRef(
                sandbox_id=sandbox_id,
                provider_id=record_provider_id,
            )
        inventory = await self.provider.list_managed()
        for orphan in await self.store.list_orphans(
            self.provider.provider_name,
            sandbox_id=sandbox_id,
        ):
            refs[orphan.provider_id] = SandboxRef(
                sandbox_id=sandbox_id,
                provider_id=orphan.provider_id,
            )
        for allocation in allocations:
            if allocation.provider_id is not None:
                refs[allocation.provider_id] = SandboxRef(
                    sandbox_id=sandbox_id,
                    provider_id=allocation.provider_id,
                )
                continue
            matches = [
                item
                for item in inventory
                if item.ref.sandbox_id == sandbox_id
                and item.metadata.get("agentbox-generation") == allocation.allocation_id
            ]
            if not matches:
                raise ProviderError(
                    "Sandbox deletion is waiting for an exact create attempt",
                    code="provider_cleanup_outcome_unknown",
                    retryable=True,
                    status_code=503,
                    headers={"Retry-After": "1"},
                )
            # Adoption requires exactly one generation. DELETE is different:
            # if a provider accepted the same durable attempt more than once,
            # every exact token match is safe and necessary to purge.
            for match in matches:
                refs[match.ref.provider_id] = match.ref
        for item in inventory:
            if item.ref.sandbox_id == sandbox_id:
                refs[item.ref.provider_id] = item.ref

        deleted = False
        for ref in refs.values():
            if not await self.provider.purge_managed(ref):
                raise ProviderError(
                    "Exact provider generation deletion is not yet confirmed",
                    code="provider_cleanup_outcome_unknown",
                    retryable=True,
                    status_code=503,
                    headers={"Retry-After": "1"},
                )
            await self.store.clear_orphan(
                self.provider.provider_name,
                ref.provider_id,
            )
            deleted = True
        return deleted

    async def _purge_storage(self, sandbox_id: str) -> bool:
        if not isinstance(self.provider, SandboxStoragePurgeProvider):
            return False
        return await self.provider.purge_storage(sandbox_id)

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
        refreshed_instances: set[str] = set()
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
                    status_code, _, _ = request_endpoint_http(
                        request,
                        timeout=2,
                        transient_gateway=endpoint.transient_gateway,
                        expected_instance_id=endpoint.instance_id,
                        expected_port=runtime.port,
                    )
                    return 200 <= status_code < 300

                if await asyncio.to_thread(check):
                    return endpoint
            except EndpointRoutingUnavailable as exc:
                last_error = exc
                refresh = getattr(self.provider, "refresh_endpoint", None)
                if refresh is not None and exc.instance_id not in refreshed_instances:
                    refreshed_instances.add(exc.instance_id)
                    try:
                        await refresh(
                            sandbox_id,
                            runtime,
                            instance_id=exc.instance_id,
                            protocol="http",
                        )
                    except ProviderError as refresh_error:
                        last_error = refresh_error
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
        matches = [item for item in inventory if item.ref.sandbox_id == sandbox_id]
        if len(matches) > 1:
            raise ProviderError(
                "Multiple provider generations exist for one logical sandbox",
                code="provider_generation_conflict",
                retryable=True,
                status_code=409,
            )
        return matches[0] if matches else None

    async def reconcile(self) -> None:
        if self._reconcile_lock.locked():
            return
        async with self._reconcile_lock:
            inventory_started_at = time.time()
            inventory = await self.provider.list_managed()
            inventory_by_sandbox: dict[str, list[ManagedSandbox]] = {}
            for item in inventory:
                inventory_by_sandbox.setdefault(item.ref.sandbox_id, []).append(item)
            policy = self.capacity_policy
            if policy is not None:
                provider_objects = {
                    item.ref.provider_id: (
                        item.ref.sandbox_id,
                        item.metadata.get("agentbox-generation"),
                        item.status.status in {"CREATING", "RUNNING"},
                    )
                    for item in inventory
                }
                await self.store.reconcile_provider_inventory(
                    policy.scope,
                    self.provider.provider_name,
                    provider_objects,
                    inventory_started_at=inventory_started_at,
                )
            else:
                # Non-capacity providers do not create hidden cloud
                # generations; retain the simpler exact-record classification.
                known_provider_ids = {
                    record.provider_id
                    for record in await self.store.list_sandboxes()
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

            records = {
                record.sandbox_id: record
                for record in await self.store.list_sandboxes()
            }

            now = time.time()
            idle_without_compute = {
                record.sandbox_id
                for record in await self.store.idle_sandboxes(
                    settings.agentbox_sandbox_idle_timeout_seconds
                )
            }
            for record in records.values():
                try:
                    items = inventory_by_sandbox.get(record.sandbox_id, [])
                    exact_item = next(
                        (
                            item
                            for item in items
                            if record.provider_id is not None
                            and item.ref.provider_id == record.provider_id
                        ),
                        None,
                    )
                    if record.desired_state == "deleted":
                        await self.delete(record.sandbox_id)
                    elif record.desired_state == "suspended":
                        if (
                            record.idle_since_at is not None
                            and now - record.idle_since_at
                            >= settings.agentbox_suspended_retention_seconds
                        ):
                            await self.delete(record.sandbox_id)
                        elif exact_item is not None and exact_item.status.ready:
                            await self.suspend(record.sandbox_id)
                    elif (
                        record.provider_id is not None
                        and exact_item is None
                        and record.sandbox_id in idle_without_compute
                    ):
                        # Recheck activity and mutate desired state while holding
                        # the same distributed fence used by ENSURE and DELETE.
                        await self.suspend_if_idle(record.sandbox_id)
                    elif (
                        exact_item is None
                        or record.observed_generation != record.desired_generation
                    ):
                        await self.ensure(
                            record.sandbox_id,
                            record.to_ensure_request(),
                        )
                except ProviderError as exc:
                    if exc.code != "lifecycle_busy":
                        raise
                    logger.debug(
                        "AgentBox reconciliation skipped busy lifecycle; sandbox_id=%s",
                        record.sandbox_id,
                    )

            expired = await self.store.expired_orphans(
                settings.agentbox_orphan_grace_seconds,
                inventory_started_at=inventory_started_at,
            )
            if isinstance(self.provider, SandboxManagedPurgeProvider):
                for orphan in expired:
                    await self._purge_expired_orphan(orphan, policy)

    async def _purge_expired_orphan(
        self,
        orphan: OrphanCandidate,
        policy: ProviderCapacityPolicy | None,
    ) -> None:
        async def purge_exact_generation() -> bool:
            ref: SandboxRef | None = None
            if orphan.sandbox_id is not None:
                ref = SandboxRef(
                    sandbox_id=orphan.sandbox_id,
                    provider_id=orphan.provider_id,
                )
            else:
                # Untagged provider objects have no stable logical lookup key.
                # A fresh inventory may recover the exact ref, but one empty
                # snapshot is never proof that the generation was deleted.
                ref = next(
                    (
                        candidate.ref
                        for candidate in await self.provider.list_managed()
                        if candidate.ref.provider_id == orphan.provider_id
                    ),
                    None,
                )
            if ref is None:
                return False
            purged = await self.provider.purge_managed(ref)  # type: ignore[union-attr]
            if not purged:
                # Provider not-found/list absence is eventually consistent and
                # cannot authorize removal of the durable orphan fence.
                return False
            if policy is not None:
                for allocation in await self.store.list_provider_allocations(
                    policy.scope
                ):
                    if allocation.provider_id == ref.provider_id:
                        await self.store.release_provider_allocation(
                            policy.scope,
                            allocation.allocation_id,
                        )
            await self.store.clear_orphan(
                orphan.provider_name,
                orphan.provider_id,
            )
            return True

        if orphan.sandbox_id is None:
            await purge_exact_generation()
            return
        record = await self.store.get_sandbox(orphan.sandbox_id)
        if record is None:
            # Install a durable deletion anchor before exact-ID purge. A first
            # ENSURE can race this insertion, but provider allocations remain
            # fenced and the lifecycle claim serializes all later mutation.
            record = await self.store.insert_sandbox_tombstone_if_missing(
                orphan.sandbox_id
            )
        async with self.claim(orphan.sandbox_id, "orphan-purge"):
            record = await self.store.get_sandbox(orphan.sandbox_id)
            allocations = (
                [
                    allocation
                    for allocation in await self.store.list_provider_allocations(
                        policy.scope
                    )
                    if allocation.sandbox_id == orphan.sandbox_id
                    and (
                        allocation.provider_id == orphan.provider_id
                        or (
                            allocation.state == "reserved"
                            and allocation.provider_id is None
                        )
                    )
                    and not allocation.allocation_id.startswith("provider:")
                ]
                if policy is not None
                else []
            )
            if (
                record is not None
                and record.desired_state != "deleted"
                and (record.provider_id == orphan.provider_id or bool(allocations))
            ):
                await self.store.clear_orphan(
                    orphan.provider_name,
                    orphan.provider_id,
                )
                return
            await purge_exact_generation()

    @staticmethod
    def _is_not_found(exc: BaseException) -> bool:
        return bool(
            isinstance(exc, ProviderError)
            and exc.status_code == 404
            or isinstance(exc, HTTPException)
            and exc.status_code == 404
        )


async def reconciliation_loop(manager: SandboxLifecycleManager) -> None:
    while True:
        await asyncio.sleep(settings.agentbox_reconcile_interval_seconds)
        try:
            await manager.reconcile()
        except Exception:
            logger.exception("AgentBox reconciliation pass failed")
