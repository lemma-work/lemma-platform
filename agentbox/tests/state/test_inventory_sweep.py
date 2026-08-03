"""Reconciling provider inventory against durable state.

This is the only code path that destroys a sandbox without a durable record
telling it to, so the tests here care as much about what it refuses to touch as
about what it cleans up.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from agentbox.domain import (
    AdmissionClass,
    AllocationState,
    SandboxKey,
    SandboxProfileRef,
    StorageKind,
    WorkloadKind,
)
from agentbox.inventory import SandboxInventorySweeper
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.maintenance import SandboxMaintenanceWorker
from agentbox.persistence.uow import StateDatabase
from agentbox.ports import (
    ProviderCreateRequest,
    ProviderCreateResult,
    ProviderInventoryAllocation,
    ProviderLifecycleError,
    ProviderMetadataEntry,
    ProviderReadyResult,
    ProviderStorageResult,
)


pytestmark = pytest.mark.asyncio


def _profile() -> SandboxProfileRef:
    return SandboxProfileRef("workspace-python-v1", f"sha256:{'a' * 64}")


@pytest_asyncio.fixture
async def database(tmp_path: Path):
    state = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await state.create_schema_for_test()
    try:
        yield state
    finally:
        await state.dispose()


class InventoryProvider:
    name = "fake"
    scope = "fake:inventory"
    workspace_storage_kind = StorageKind.VOLUME

    def __init__(self, database: StateDatabase) -> None:
        self.database = database
        self.inventory: list[ProviderInventoryAllocation] = []
        self.destroyed: list[str] = []
        self.released: list[str] = []
        self.listing_error: Exception | None = None
        self.destroy_error: Exception | None = None
        self._created = 0

    async def create(self, request: ProviderCreateRequest) -> ProviderCreateResult:
        self._created += 1
        provider_id = f"sandbox-{self._created}"
        return ProviderCreateResult(
            provider_id=provider_id,
            provider_instance_id=provider_id,
            provider_request_id=None,
            workspace_storage=(
                ProviderStorageResult(
                    provider_storage_id=f"volume-{self._created}",
                    bound_to_allocation=False,
                )
                if request.workspace_storage is not None
                else None
            ),
        )

    async def wait_ready(self, allocation, *, profile, deadline_at):
        del profile, deadline_at
        return ProviderReadyResult(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_instance_id,
        )

    async def find_allocations(self, metadata, *, deadline_at):
        del metadata, deadline_at
        if self.listing_error is not None:
            raise self.listing_error
        return tuple(self.inventory)

    async def destroy_allocation(self, allocation, *, deadline_at) -> None:
        del deadline_at
        if self.destroy_error is not None:
            raise self.destroy_error
        self.destroyed.append(allocation.provider_id)

    async def release_allocation(self, allocation, *, deadline_at) -> None:
        del deadline_at
        self.released.append(allocation.provider_id)

    async def destroy_workspace_storage(self, provider_storage_id, *, deadline_at):
        del provider_storage_id, deadline_at

    async def close(self) -> None:
        return None


def _sweeper(database, provider, *, grace: float = 0.0) -> SandboxInventorySweeper:
    return SandboxInventorySweeper(
        database, provider, untracked_grace_seconds=grace
    )


async def _ensure(database: StateDatabase, provider) -> tuple[SandboxKey, UUID, str]:
    lifecycle = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    await lifecycle.ensure(
        key,
        _profile(),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    async with database.uow() as uow:
        allocation = await uow.repository.current_allocation(key)
        await uow.commit()
    assert allocation is not None and allocation.provider_id is not None
    return key, allocation.allocation_token, allocation.provider_id


async def test_a_sandbox_this_database_never_created_is_left_alone(
    database: StateDatabase,
):
    """A shared provider account means unrecognised does not mean abandoned.

    Environments routinely share one provider account, so a token this database
    has never seen belongs to another deployment. The database is the reliable
    record - rows do not vanish on their own - so destroying here would delete
    somebody else's live sandboxes.
    """

    provider = InventoryProvider(database)
    provider.inventory = [
        ProviderInventoryAllocation(
            provider_id="someone-elses-1",
            provider_instance_id="someone-elses-1",
            allocation_token=uuid4(),
            running=True,
        )
    ]

    swept = await _sweeper(database, provider).sweep_once(
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30)
    )

    assert swept == 0
    assert provider.destroyed == []


async def test_a_live_sandbox_is_never_touched(database: StateDatabase):
    provider = InventoryProvider(database)
    _key, token, provider_id = await _ensure(database, provider)
    provider.inventory = [
        ProviderInventoryAllocation(
            provider_id=provider_id,
            provider_instance_id=provider_id,
            allocation_token=token,
            running=True,
        )
    ]

    swept = await _sweeper(database, provider).sweep_once(
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30)
    )

    assert swept == 0
    assert provider.destroyed == []


async def test_a_released_workspace_keeps_its_paused_sandbox(
    database: StateDatabase,
):
    """A released workspace is paused, not gone - its filesystem is user data.

    Treating a paused sandbox as unowned would delete exactly the files the
    release exists to preserve.
    """

    provider = InventoryProvider(database)
    lifecycle = SandboxLifecycleService(database, provider)
    key, token, provider_id = await _ensure(database, provider)
    await lifecycle.release(
        key, deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30)
    )
    provider.inventory = [
        ProviderInventoryAllocation(
            provider_id=provider_id,
            provider_instance_id=provider_id,
            allocation_token=token,
            running=False,
        )
    ]

    swept = await _sweeper(database, provider).sweep_once(
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30)
    )

    assert swept == 0
    assert provider.destroyed == []


async def test_a_destroyed_allocation_leaves_no_provider_object_behind(
    database: StateDatabase,
):
    """If durable state says destroyed, a surviving object is still billing."""

    provider = InventoryProvider(database)
    lifecycle = SandboxLifecycleService(
        database, provider, workspace_retention_seconds=0
    )
    maintenance = SandboxMaintenanceWorker(
        database,
        lifecycle,
        workspace_idle_seconds=0,
        function_idle_seconds=0,
        batch_size=1,
    )
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    _key, token, provider_id = await _ensure(database, provider)
    assert await maintenance.run_once(deadline_at=deadline) == 1
    assert await maintenance.run_once(deadline_at=deadline) == 1
    provider.destroyed.clear()

    # The provider still reports it, so the destroy did not actually take.
    provider.inventory = [
        ProviderInventoryAllocation(
            provider_id=provider_id,
            provider_instance_id=provider_id,
            allocation_token=token,
            running=True,
        )
    ]
    swept = await _sweeper(database, provider).sweep_once(deadline_at=deadline)

    assert swept == 1
    assert provider.destroyed == [provider_id]


async def _reclaimable(database: StateDatabase, provider) -> ProviderInventoryAllocation:
    """Ours, already destroyed in durable state, yet still running."""

    lifecycle = SandboxLifecycleService(
        database, provider, workspace_retention_seconds=0
    )
    maintenance = SandboxMaintenanceWorker(
        database,
        lifecycle,
        workspace_idle_seconds=0,
        function_idle_seconds=0,
        batch_size=1,
    )
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    _key, token, provider_id = await _ensure(database, provider)
    assert await maintenance.run_once(deadline_at=deadline) == 1
    assert await maintenance.run_once(deadline_at=deadline) == 1
    provider.destroyed.clear()
    return ProviderInventoryAllocation(
        provider_id=provider_id,
        provider_instance_id=provider_id,
        allocation_token=token,
        running=True,
    )


async def test_reclaiming_waits_for_the_grace_period(database: StateDatabase):
    """Never act on first sight: this can race a destroy already in progress."""

    provider = InventoryProvider(database)
    provider.inventory = [await _reclaimable(database, provider)]
    sweeper = _sweeper(database, provider, grace=3600)

    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    assert await sweeper.sweep_once(deadline_at=deadline) == 0
    assert await sweeper.sweep_once(deadline_at=deadline) == 0
    assert provider.destroyed == []


async def test_a_failed_listing_never_destroys_anything(database: StateDatabase):
    """A partial or failed listing must not be read as 'nothing is owned'."""

    provider = InventoryProvider(database)
    provider.listing_error = ProviderLifecycleError("provider is unavailable")

    swept = await _sweeper(database, provider).sweep_once(
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30)
    )

    assert swept == 0
    assert provider.destroyed == []


async def test_a_provider_side_pause_is_recorded_against_durable_state(
    database: StateDatabase,
):
    """A provider can stop a sandbox we believe is ACTIVE.

    Durable state then keeps an unchanged epoch, so runtime routing hands out
    handles to processes and interpreters the pause already destroyed. Marking
    it released makes the next ensure resume it under a fresh epoch.
    """

    provider = InventoryProvider(database)
    key, token, provider_id = await _ensure(database, provider)
    provider.inventory = [
        ProviderInventoryAllocation(
            provider_id=provider_id,
            provider_instance_id=provider_id,
            allocation_token=token,
            running=False,
        )
    ]

    swept = await _sweeper(database, provider).sweep_once(
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30)
    )

    assert swept == 1
    assert provider.destroyed == []
    async with database.uow() as uow:
        allocation = await uow.repository.current_allocation(key)
        await uow.commit()
    assert allocation is not None
    assert allocation.state == AllocationState.RELEASED


async def test_resuming_after_a_provider_pause_assigns_a_fresh_epoch(
    database: StateDatabase,
):
    """The point of recording the pause: stale handles must be fenced."""

    provider = InventoryProvider(database)
    lifecycle = SandboxLifecycleService(database, provider)
    key, token, provider_id = await _ensure(database, provider)
    async with database.uow() as uow:
        before = await uow.repository.get_logical(key)
        await uow.commit()
    assert before is not None
    provider.inventory = [
        ProviderInventoryAllocation(
            provider_id=provider_id,
            provider_instance_id=provider_id,
            allocation_token=token,
            running=False,
        )
    ]
    await _sweeper(database, provider).sweep_once(
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30)
    )

    resumed = await lifecycle.ensure(
        key,
        _profile(),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )

    assert resumed.ready is True
    assert resumed.allocation_epoch > before.allocation_epoch


async def test_a_provider_object_without_a_token_is_left_alone(
    database: StateDatabase,
):
    """We stamp a token on everything we create, so no token means not ours."""

    provider = InventoryProvider(database)
    provider.inventory = [
        ProviderInventoryAllocation(
            provider_id="foreign-1",
            provider_instance_id="foreign-1",
            allocation_token=None,
            running=True,
        )
    ]

    swept = await _sweeper(database, provider).sweep_once(
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30)
    )

    assert swept == 0
    assert provider.destroyed == []


async def test_a_failed_destroy_is_retried_on_the_next_sweep(
    database: StateDatabase,
):
    provider = InventoryProvider(database)
    item = await _reclaimable(database, provider)
    provider.inventory = [item]
    sweeper = _sweeper(database, provider)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)

    provider.destroy_error = ProviderLifecycleError("provider is unavailable")
    assert await sweeper.sweep_once(deadline_at=deadline) == 0

    provider.destroy_error = None
    assert await sweeper.sweep_once(deadline_at=deadline) == 1
    assert provider.destroyed == [item.provider_id]


async def test_the_sweep_asks_only_for_its_own_provider_scope(
    database: StateDatabase,
):
    """A scope-wide destroy loose in a shared account would be catastrophic."""

    provider = InventoryProvider(database)
    seen: list[tuple[ProviderMetadataEntry, ...]] = []

    async def record(metadata, *, deadline_at):
        del deadline_at
        seen.append(metadata)
        return ()

    provider.find_allocations = record  # type: ignore[assignment]
    await _sweeper(database, provider).sweep_once(
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30)
    )

    assert len(seen) == 1
    assert {item.name: item.value for item in seen[0]} == {
        "managed-by": "agentbox",
        "provider-scope": provider.scope,
    }
