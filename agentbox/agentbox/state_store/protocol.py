from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentbox.schemas import SandboxEnsureRequest

from .models import (
    ActivityLease,
    LifecycleClaim,
    OrphanCandidate,
    ProviderAllocation,
    SandboxRecord,
    SessionRecord,
    DesiredSandboxState,
    ObservedSandboxState,
)


@runtime_checkable
class AsyncStateStore(Protocol):
    """Durable manager state independent of a sandbox provider.

    Implementations must make lifecycle claims atomic across manager replicas.
    Activity leases deliberately expire so a crashed manager cannot keep a
    sandbox alive forever.
    """

    async def healthcheck(self) -> None: ...

    async def upsert_sandbox(
        self, sandbox_id: str, request: SandboxEnsureRequest
    ) -> SandboxRecord: ...

    async def insert_sandbox_if_missing(self, sandbox_id: str) -> SandboxRecord: ...
    async def insert_sandbox_tombstone_if_missing(
        self, sandbox_id: str
    ) -> SandboxRecord: ...
    async def ensure_sandbox_defaults(self, sandbox_id: str) -> SandboxRecord: ...
    async def get_sandbox(self, sandbox_id: str) -> SandboxRecord | None: ...
    async def list_sandboxes(self) -> list[SandboxRecord]: ...
    async def delete_sandbox(self, sandbox_id: str) -> None: ...
    async def set_sandbox_desired_state(
        self, sandbox_id: str, desired_state: DesiredSandboxState
    ) -> SandboxRecord | None: ...
    async def set_sandbox_observation(
        self,
        sandbox_id: str,
        *,
        provider_name: str,
        provider_id: str,
        instance_id: str | None,
        observed_generation: int,
        observed_state: ObservedSandboxState = "running",
        status_data: dict[str, object] | None = None,
        endpoint_data: dict[str, object] | None = None,
    ) -> SandboxRecord | None: ...
    async def set_sandbox_observed_state(
        self,
        sandbox_id: str,
        *,
        observed_state: ObservedSandboxState,
        expected_generation: int,
        expected_observed_state: ObservedSandboxState | None = None,
        last_failure: str | None = None,
        reconcile_after: float | None = None,
    ) -> SandboxRecord | None: ...
    async def set_sandbox_provider_identity(
        self,
        sandbox_id: str,
        *,
        provider_name: str,
        provider_id: str,
        instance_id: str | None,
        desired_generation: int,
    ) -> SandboxRecord | None: ...
    async def clear_sandbox_provider_identity(
        self,
        sandbox_id: str,
        *,
        provider_id: str,
        desired_generation: int,
    ) -> SandboxRecord | None: ...

    async def upsert_session(
        self,
        sandbox_id: str,
        session_id: str,
        *,
        cwd: str,
        env_keys: list[str],
        expected_generation: int | None = None,
    ) -> SessionRecord | None: ...

    async def touch_session(
        self, sandbox_id: str, session_id: str, *, owner: str | None = None
    ) -> bool: ...
    async def get_session(
        self, sandbox_id: str, session_id: str
    ) -> SessionRecord | None: ...
    async def delete_session(self, sandbox_id: str, session_id: str) -> bool: ...
    async def delete_sandbox_sessions(self, sandbox_id: str) -> int: ...
    async def expired_sessions(
        self, idle_timeout_seconds: int
    ) -> list[SessionRecord]: ...
    async def idle_sandboxes(
        self, idle_timeout_seconds: int
    ) -> list[SandboxRecord]: ...
    async def mark_sandbox_active(
        self, sandbox_id: str, *, owner: str | None = None
    ) -> bool: ...
    async def mark_pod_stopped(
        self,
        sandbox_id: str,
        *,
        expected_provider_id: str | None = None,
        expected_desired_generation: int | None = None,
    ) -> SandboxRecord | None: ...
    async def finalize_sandbox_suspend(
        self,
        sandbox_id: str,
        *,
        expected_provider_id: str,
        expected_desired_generation: int,
        previous_observed_generation: int,
        claim_id: str,
        claim_owner: str,
        provider_scope: str | None,
    ) -> SandboxRecord | None: ...
    async def begin_sandbox_suspend(
        self,
        sandbox_id: str,
        *,
        idle_timeout_seconds: int,
        require_no_sessions: bool = True,
    ) -> SandboxRecord | None: ...
    async def mark_idle_if_empty(self, sandbox_id: str) -> None: ...

    async def acquire_activity_lease(
        self,
        sandbox_id: str,
        *,
        session_id: str | None,
        operation: str,
        owner: str,
        ttl_seconds: float,
        touch_activity: bool = True,
    ) -> ActivityLease | None: ...

    async def renew_activity_lease(
        self, lease_id: str, *, owner: str, ttl_seconds: float
    ) -> ActivityLease | None: ...
    async def release_activity_lease(self, lease_id: str, *, owner: str) -> bool: ...
    async def prune_expired_activity_leases(self) -> int: ...

    async def has_active_activity_lease(
        self,
        sandbox_id: str,
        *,
        session_id: str | None = None,
    ) -> bool: ...

    async def acquire_lifecycle_claim(
        self,
        sandbox_id: str,
        *,
        operation: str,
        owner: str,
        ttl_seconds: float,
    ) -> LifecycleClaim | None: ...

    async def renew_lifecycle_claim(
        self, claim_id: str, *, owner: str, ttl_seconds: float
    ) -> LifecycleClaim | None: ...
    async def release_lifecycle_claim(self, claim_id: str, *, owner: str) -> bool: ...
    async def get_lifecycle_claim(self, sandbox_id: str) -> LifecycleClaim | None: ...

    async def observe_orphan(
        self,
        provider_name: str,
        provider_id: str,
        *,
        sandbox_id: str | None,
        observed_at: float | None = None,
    ) -> OrphanCandidate: ...

    async def expired_orphans(
        self,
        grace_seconds: float,
        *,
        inventory_started_at: float,
    ) -> list[OrphanCandidate]: ...
    async def list_orphans(
        self,
        provider_name: str,
        *,
        sandbox_id: str | None = None,
    ) -> list[OrphanCandidate]: ...
    async def clear_orphan(self, provider_name: str, provider_id: str) -> bool: ...

    async def reserve_provider_allocation(
        self,
        provider_scope: str,
        sandbox_id: str,
        *,
        owner: str,
        max_active: int,
        ttl_seconds: float,
    ) -> ProviderAllocation | None: ...

    async def activate_provider_allocation(
        self,
        provider_scope: str,
        allocation_id: str,
        *,
        owner: str,
        provider_id: str,
    ) -> ProviderAllocation | None: ...

    async def hold_provider_allocation(
        self,
        provider_scope: str,
        allocation_id: str,
        *,
        owner: str,
    ) -> ProviderAllocation | None: ...

    async def release_provider_allocation(
        self, provider_scope: str, allocation_id: str
    ) -> bool: ...

    async def list_provider_allocations(
        self, provider_scope: str
    ) -> list[ProviderAllocation]: ...

    async def reconcile_provider_allocations(
        self,
        provider_scope: str,
        active_provider_objects: dict[str, tuple[str, str | None]],
        *,
        inventory_started_at: float,
    ) -> None: ...

    async def reconcile_provider_inventory(
        self,
        provider_scope: str,
        provider_name: str,
        provider_objects: dict[str, tuple[str, str | None, bool]],
        *,
        inventory_started_at: float,
    ) -> None:
        """Atomically publish allocation and orphan evidence from one snapshot."""
        ...

    async def close(self) -> None: ...
