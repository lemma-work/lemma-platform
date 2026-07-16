from __future__ import annotations

import asyncio
import time

import pytest

from agentbox.api import lifecycle as lifecycle_module
from agentbox.api.lifecycle import _renew_activity_lease, activity_lease
from agentbox.config import settings
from agentbox.lifecycle_manager import SandboxLifecycleManager
from agentbox.providers.errors import ProviderError
from agentbox.providers.models import (
    ManagedSandbox,
    ProviderCapabilities,
    ProviderCapacityPolicy,
    SandboxEndpoint,
    SandboxRef,
)
from agentbox.schemas import SandboxEnsureRequest, SandboxInternalStatus
from agentbox.state_store.models import ActivityLease
from agentbox.state_store.sqlite import SQLiteStateStore


class FakeLifecycleProvider:
    provider_name = "fake-cloud"

    def __init__(self) -> None:
        self.objects: dict[str, str] = {}
        self.create_started = asyncio.Event()
        self.allow_create = asyncio.Event()
        self.allow_create.set()
        self.create_count = 0
        self.release_count = 0
        self.delete_count = 0
        self.requests: dict[str, dict[str, str]] = {}
        self.stopped: set[str] = set()
        self.generation_tokens: dict[str, str] = {}
        self.deleted_sessions: list[tuple[str, str]] = []

    @property
    def capacity_policy(self) -> ProviderCapacityPolicy:
        return ProviderCapacityPolicy(scope="fake:test", max_active=10)

    async def create(self, sandbox_id, request):
        self.requests[sandbox_id] = dict(request.env)
        self.create_started.set()
        await self.allow_create.wait()
        self.create_count += 1
        self.objects.setdefault(sandbox_id, f"provider-{sandbox_id}")
        self.stopped.discard(sandbox_id)
        return self._status(sandbox_id, ready=True)

    async def get_status(self, sandbox_id):
        return self._status(
            sandbox_id,
            ready=sandbox_id in self.objects and sandbox_id not in self.stopped,
        )

    async def create_generation(
        self,
        sandbox_id,
        request,
        *,
        generation_token: str,
    ):
        self.generation_tokens[sandbox_id] = generation_token
        return await self.create(sandbox_id, request)

    async def list_managed(self):
        return [
            ManagedSandbox(
                ref=SandboxRef(sandbox_id=sandbox_id, provider_id=provider_id),
                status=self._status(
                    sandbox_id,
                    ready=sandbox_id not in self.stopped,
                ),
                instance_id=provider_id,
                metadata={
                    "agentbox-generation": self.generation_tokens.get(sandbox_id, "")
                },
            )
            for sandbox_id, provider_id in self.objects.items()
        ]

    async def resolve_endpoint(self, sandbox_id, app, *, protocol="http"):
        del app, protocol
        return SandboxEndpoint(
            base_url="http://runtime.test",
            instance_id=self.objects.get(sandbox_id),
            provider_id=self.objects.get(sandbox_id),
        )

    async def release(self, sandbox_id):
        if sandbox_id not in self.objects:
            return False
        self.release_count += 1
        self.stopped.add(sandbox_id)
        return True

    async def delete(self, sandbox_id):
        self.delete_count += 1
        self.stopped.discard(sandbox_id)
        return self.objects.pop(sandbox_id, None) is not None

    async def delete_session(self, sandbox_id: str, session_id: str) -> bool:
        self.deleted_sessions.append((sandbox_id, session_id))
        return True

    async def close(self):
        return None

    @staticmethod
    def _status(sandbox_id: str, *, ready: bool) -> SandboxInternalStatus:
        return SandboxInternalStatus(
            id=sandbox_id,
            ready=ready,
            status="RUNNING" if ready else "STOPPED",
            runtime_url="http://runtime.test" if ready else None,
        )


class LeaseLifecycleProvider(FakeLifecycleProvider):
    def __init__(self) -> None:
        super().__init__()
        self.lease_renewals: list[str] = []

    async def renew_lease(self, sandbox_id: str) -> None:
        self.lease_renewals.append(sandbox_id)


class StorageLifecycleProvider(FakeLifecycleProvider):
    def __init__(self) -> None:
        super().__init__()
        self.storage: set[str] = set()
        self.storage_purges: list[str] = []

    async def create(self, sandbox_id, request):
        self.storage.add(sandbox_id)
        return await super().create(sandbox_id, request)

    async def purge_storage(self, sandbox_id: str) -> bool:
        self.storage_purges.append(sandbox_id)
        existed = sandbox_id in self.storage
        self.storage.discard(sandbox_id)
        return existed


class BlockingStoragePurgeProvider(StorageLifecycleProvider):
    def __init__(self) -> None:
        super().__init__()
        self.purge_started = asyncio.Event()
        self.allow_purge = asyncio.Event()

    async def purge_storage(self, sandbox_id: str) -> bool:
        self.purge_started.set()
        await self.allow_purge.wait()
        return await super().purge_storage(sandbox_id)


class InventoryLagProvider(FakeLifecycleProvider):
    def __init__(self, *, expose_provider_id: bool) -> None:
        super().__init__()
        self.expose_provider_id = expose_provider_id

    async def list_managed(self):
        return []

    async def resolve_endpoint(self, sandbox_id, app, *, protocol="http"):
        del app, protocol
        provider_id = self.objects.get(sandbox_id)
        return SandboxEndpoint(
            base_url="http://runtime.test",
            instance_id=provider_id,
            provider_id=provider_id if self.expose_provider_id else None,
        )


class ConflictingEndpointIdentityProvider(FakeLifecycleProvider):
    async def resolve_endpoint(self, sandbox_id, app, *, protocol="http"):
        endpoint = await super().resolve_endpoint(sandbox_id, app, protocol=protocol)
        return SandboxEndpoint(
            base_url=endpoint.base_url,
            instance_id=endpoint.instance_id,
            provider_id="stale-endpoint-provider-id",
        )


class AmbiguousLifecycleProvider(FakeLifecycleProvider):
    capabilities = ProviderCapabilities(
        stable_release_identity=True,
        release_preserves_filesystem=True,
        private_egress_isolation=True,
        authenticated_http=True,
        authenticated_websocket=True,
    )

    def __init__(self) -> None:
        super().__init__()
        self.fail_after_accept = False
        self.hide_inventory = False
        self.adopt_count = 0

    async def create(self, sandbox_id, request):
        status = await super().create(sandbox_id, request)
        if self.fail_after_accept:
            self.fail_after_accept = False
            raise ProviderError(
                "accepted then disconnected",
                code="provider_create_outcome_unknown",
                retryable=True,
            )
        return status

    async def list_managed(self):
        if self.hide_inventory:
            return []
        return await super().list_managed()

    async def adopt(self, sandbox_id: str, provider_id: str) -> bool:
        self.adopt_count += 1
        if self.objects.get(sandbox_id) != provider_id:
            raise ProviderError(
                "generation unavailable",
                code="provider_adoption_unavailable",
                retryable=True,
                status_code=503,
            )
        return True


class BootstrapLifecycleProvider(AmbiguousLifecycleProvider):
    def __init__(self) -> None:
        super().__init__()
        self.bootstrap_count = 0

    async def bootstrap(self, sandbox_id, request) -> None:
        del sandbox_id, request
        self.bootstrap_count += 1


class EventuallyConsistentPurgeProvider(AmbiguousLifecycleProvider):
    def __init__(self) -> None:
        super().__init__()
        self.purge_visible = False
        self.purge_attempts: list[SandboxRef] = []

    async def purge_managed(self, ref: SandboxRef) -> bool:
        self.purge_attempts.append(ref)
        if not self.purge_visible:
            return False
        if self.objects.get(ref.sandbox_id) != ref.provider_id:
            return False
        del self.objects[ref.sandbox_id]
        return True


class DuplicateInventoryProvider(AmbiguousLifecycleProvider):
    def __init__(self) -> None:
        super().__init__()
        self.duplicates: dict[str, str] = {}
        self.duplicate_tokens: dict[str, str] = {}
        self.purged: list[SandboxRef] = []

    async def list_managed(self):
        managed = await super().list_managed()
        managed.extend(
            ManagedSandbox(
                ref=SandboxRef(sandbox_id=sandbox_id, provider_id=provider_id),
                status=self._status(sandbox_id, ready=True),
                instance_id=provider_id,
                metadata=(
                    {"agentbox-generation": self.duplicate_tokens[provider_id]}
                    if provider_id in self.duplicate_tokens
                    else {}
                ),
            )
            for provider_id, sandbox_id in self.duplicates.items()
        )
        return managed

    async def purge_managed(self, ref: SandboxRef) -> bool:
        self.purged.append(ref)
        if self.duplicates.get(ref.provider_id) == ref.sandbox_id:
            del self.duplicates[ref.provider_id]
            self.duplicate_tokens.pop(ref.provider_id, None)
            return True
        if self.objects.get(ref.sandbox_id) == ref.provider_id:
            del self.objects[ref.sandbox_id]
            return True
        return False


class DefinitiveRejectProvider(AmbiguousLifecycleProvider):
    async def create_generation(
        self,
        sandbox_id,
        request,
        *,
        generation_token: str,
    ):
        del sandbox_id, request, generation_token
        raise ProviderError(
            "provider rejected before acceptance",
            code="provider_create_rejected",
            retryable=False,
            status_code=400,
        )


async def _manager(tmp_path, provider=None):
    store = await SQLiteStateStore.open(
        str(tmp_path / "state.db"),
        durable_env_keys=settings.agentbox_state_durable_env_key_set,
    )
    provider = provider or FakeLifecycleProvider()
    manager = SandboxLifecycleManager(provider, store, owner="manager-a")

    async def ready(sandbox_id: str):
        return await provider.resolve_endpoint(
            sandbox_id,
            None,
        )

    manager._wait_until_runtime_ready = ready  # type: ignore[method-assign]
    return manager, provider, store


@pytest.mark.asyncio
async def test_endpoint_provider_id_bridges_successful_inventory_lag(tmp_path):
    provider = InventoryLagProvider(expose_provider_id=True)
    manager, _, store = await _manager(tmp_path, provider)
    try:
        status = await manager.ensure("sandbox", SandboxEnsureRequest())

        assert status.ready is True
        record = await store.get_sandbox("sandbox")
        assert record is not None
        assert record.provider_id == "provider-sandbox"
        allocations = await store.list_provider_allocations("fake:test")
        assert [(row.state, row.provider_id) for row in allocations] == [
            ("active", "provider-sandbox")
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_inventory_lag_without_endpoint_provider_id_fails_closed(tmp_path):
    provider = InventoryLagProvider(expose_provider_id=False)
    manager, _, store = await _manager(tmp_path, provider)
    try:
        with pytest.raises(ProviderError) as error:
            await manager.ensure("sandbox", SandboxEnsureRequest())

        assert error.value.code == "provider_observation_unknown"
        record = await store.get_sandbox("sandbox")
        assert record is not None and record.provider_id is None
        allocations = await store.list_provider_allocations("fake:test")
        assert [(row.state, row.provider_id) for row in allocations] == [
            ("reserved", None)
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_inventory_and_endpoint_identity_mismatch_fails_closed(tmp_path):
    provider = ConflictingEndpointIdentityProvider()
    manager, _, store = await _manager(tmp_path, provider)
    try:
        with pytest.raises(ProviderError) as error:
            await manager.ensure("sandbox", SandboxEnsureRequest())

        assert error.value.code == "endpoint_generation_changed"
        record = await store.get_sandbox("sandbox")
        assert record is not None
        assert record.provider_id == "stale-endpoint-provider-id"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_state_store_failure_after_create_keeps_exact_attempt_fenced(
    tmp_path,
    monkeypatch,
):
    provider = AmbiguousLifecycleProvider()
    manager, _, store = await _manager(tmp_path, provider)
    original_set_identity = store.set_sandbox_provider_identity
    failed = False

    async def fail_once(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("database connection lost after provider acceptance")
        return await original_set_identity(*args, **kwargs)

    monkeypatch.setattr(store, "set_sandbox_provider_identity", fail_once)
    try:
        with pytest.raises(OSError):
            await manager.ensure("sandbox", SandboxEnsureRequest())

        allocations = await store.list_provider_allocations("fake:test")
        assert len(allocations) == 1
        assert allocations[0].state == "reserved"
        assert allocations[0].owner == "agentbox:outcome-unknown"
        assert provider.create_count == 1

        provider.hide_inventory = True
        with pytest.raises(ProviderError) as hidden:
            await manager.ensure("sandbox", SandboxEnsureRequest())
        assert hidden.value.code == "provider_create_outcome_unknown"
        assert provider.create_count == 1

        provider.hide_inventory = False
        status = await manager.ensure("sandbox", SandboxEnsureRequest())
        assert status.ready is True
        assert provider.create_count == 1
        record = await store.get_sandbox("sandbox")
        assert record is not None
        assert record.provider_id == "provider-sandbox"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_definitive_pre_acceptance_rejection_releases_attempt(tmp_path):
    provider = DefinitiveRejectProvider()
    manager, _, store = await _manager(tmp_path, provider)
    try:
        with pytest.raises(ProviderError) as rejected:
            await manager.ensure("sandbox", SandboxEnsureRequest())

        assert rejected.value.code == "provider_create_rejected"
        assert await store.list_provider_allocations("fake:test") == []
        assert provider.objects == {}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_ambiguous_create_remains_fenced_across_manager_restart(tmp_path):
    provider = AmbiguousLifecycleProvider()
    manager, _, store = await _manager(tmp_path, provider)
    provider.fail_after_accept = True
    provider.hide_inventory = True
    try:
        with pytest.raises(ProviderError) as first:
            await manager.ensure("sandbox", SandboxEnsureRequest())
        assert first.value.code == "provider_create_outcome_unknown"
        allocations = await store.list_provider_allocations("fake:test")
        assert len(allocations) == 1
        assert allocations[0].state == "reserved"
        assert allocations[0].owner == "agentbox:outcome-unknown"

        restarted = SandboxLifecycleManager(provider, store, owner="manager-b")
        restarted._wait_until_runtime_ready = manager._wait_until_runtime_ready  # type: ignore[method-assign]
        with pytest.raises(ProviderError) as hidden:
            await restarted.ensure("sandbox", SandboxEnsureRequest())
        assert hidden.value.code == "provider_create_outcome_unknown"
        assert provider.create_count == 1

        provider.hide_inventory = False
        status = await restarted.ensure("sandbox", SandboxEnsureRequest())
        assert status.ready is True
        assert provider.create_count == 1
        assert provider.adopt_count == 1
        record = await store.get_sandbox("sandbox")
        assert record is not None
        assert record.provider_id == "provider-sandbox"
        allocations = await store.list_provider_allocations("fake:test")
        assert [(row.state, row.provider_id) for row in allocations] == [
            ("active", "provider-sandbox")
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_ambiguous_create_blocks_environment_replacement(tmp_path):
    provider = AmbiguousLifecycleProvider()
    manager, _, store = await _manager(tmp_path, provider)
    provider.fail_after_accept = True
    provider.hide_inventory = True
    try:
        with pytest.raises(ProviderError):
            await manager.ensure(
                "sandbox",
                SandboxEnsureRequest(env={"LEMMA_BASE_URL": "https://one.test"}),
            )

        with pytest.raises(ProviderError) as changed:
            await manager.ensure(
                "sandbox",
                SandboxEnsureRequest(env={"LEMMA_BASE_URL": "https://two.test"}),
            )

        assert changed.value.code == "provider_create_outcome_unknown"
        assert provider.create_count == 1
        record = await store.get_sandbox("sandbox")
        assert record is not None
        assert record.env["LEMMA_BASE_URL"] == "https://one.test"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_reconcile_defers_legacy_env_upgrade_with_unresolved_allocation(
    tmp_path,
    caplog,
):
    provider = AmbiguousLifecycleProvider()
    manager, _, store = await _manager(tmp_path, provider)
    try:
        # Simulate a record written before function concurrency became part of
        # the durable sandbox environment, plus a create whose provider outcome
        # is still unknown. Startup must preserve that fence without crashing.
        await store.upsert_sandbox(
            "legacy-sandbox",
            SandboxEnsureRequest(env={"LEMMA_BASE_URL": "https://lemma.test"}),
        )
        allocation = await store.reserve_provider_allocation(
            "fake:test",
            "legacy-sandbox",
            owner="manager-that-exited",
            max_active=10,
            ttl_seconds=600,
        )
        assert allocation is not None
        held = await store.hold_provider_allocation(
            "fake:test",
            allocation.allocation_id,
            owner="agentbox:outcome-unknown",
        )
        assert held is not None

        with caplog.at_level("WARNING"):
            await manager.reconcile()

        record = await store.get_sandbox("legacy-sandbox")
        assert record is not None
        assert record.env == {"LEMMA_BASE_URL": "https://lemma.test"}
        allocations = await store.list_provider_allocations("fake:test")
        assert [(row.state, row.provider_id) for row in allocations] == [
            ("reserved", None)
        ]
        assert provider.create_count == 0
        assert "sandbox_id=legacy-sandbox" in caplog.text
        assert "code=provider_create_outcome_unknown" in caplog.text
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_create_intent_without_observed_compute_never_retries_create(tmp_path):
    provider = AmbiguousLifecycleProvider()
    manager, _, store = await _manager(tmp_path, provider)
    try:
        await store.insert_sandbox_if_missing("sandbox")
        allocation = await store.reserve_provider_allocation(
            "fake:test",
            "sandbox",
            owner="manager-that-exited",
            max_active=10,
            ttl_seconds=600,
        )
        assert allocation is not None
        held = await store.hold_provider_allocation(
            "fake:test",
            allocation.allocation_id,
            owner="agentbox:outcome-unknown",
        )
        assert held is not None

        with pytest.raises(ProviderError) as error:
            await manager.ensure("sandbox", SandboxEnsureRequest())

        assert error.value.code == "provider_create_outcome_unknown"
        assert provider.create_count == 0
        allocations = await store.list_provider_allocations("fake:test")
        assert [(row.state, row.owner) for row in allocations] == [
            ("reserved", "agentbox:outcome-unknown")
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_reconcile_adopts_ambiguous_generation_without_second_create(tmp_path):
    provider = AmbiguousLifecycleProvider()
    manager, _, store = await _manager(tmp_path, provider)
    provider.fail_after_accept = True
    try:
        with pytest.raises(ProviderError) as error:
            await manager.ensure("sandbox", SandboxEnsureRequest())
        assert error.value.code == "provider_create_outcome_unknown"

        await manager.reconcile()

        record = await store.get_sandbox("sandbox")
        assert record is not None
        assert record.provider_id == "provider-sandbox"
        assert provider.create_count == 1
        assert provider.adopt_count == 1
        allocations = await store.list_provider_allocations("fake:test")
        assert [(row.state, row.provider_id) for row in allocations] == [
            ("active", "provider-sandbox")
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_repeated_ensure_does_not_rebootstrap_observed_generation(tmp_path):
    provider = BootstrapLifecycleProvider()
    manager, _, store = await _manager(tmp_path, provider)
    try:
        first = await manager.ensure("sandbox", SandboxEnsureRequest())
        second = await manager.ensure("sandbox", SandboxEnsureRequest())

        assert first.ready is True
        assert second.ready is True
        assert provider.create_count == 1
        assert provider.bootstrap_count == 1
        assert provider.adopt_count == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_reconcile_skips_sandbox_with_busy_lifecycle_claim(
    tmp_path,
    monkeypatch,
):
    provider = FakeLifecycleProvider()
    manager, _, store = await _manager(tmp_path, provider)
    try:
        await store.insert_sandbox_if_missing("sandbox")
        claim = await store.acquire_lifecycle_claim(
            "sandbox",
            operation="ensure",
            owner="other-manager",
            ttl_seconds=60,
        )
        assert claim is not None
        monkeypatch.setattr(
            settings,
            "agentbox_lifecycle_claim_wait_seconds",
            0.01,
        )

        await manager.reconcile()

        assert provider.create_count == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_orphan_absence_retains_tombstone_until_exact_purge(tmp_path):
    provider = EventuallyConsistentPurgeProvider()
    manager, _, store = await _manager(tmp_path, provider)
    provider.objects["sandbox"] = "provider-sandbox"
    now = time.time()
    try:
        await store.reconcile_provider_allocations(
            "fake:test",
            {"provider-sandbox": ("sandbox", None)},
            inventory_started_at=now - 100,
        )
        await store.observe_orphan(
            provider.provider_name,
            "provider-sandbox",
            sandbox_id="sandbox",
            observed_at=now - 100,
        )
        [orphan] = await store.expired_orphans(10, inventory_started_at=now)

        await manager._purge_expired_orphan(orphan, provider.capacity_policy)

        tombstone = await store.get_sandbox("sandbox")
        assert tombstone is not None
        assert tombstone.desired_state == "deleted"
        assert provider.objects == {"sandbox": "provider-sandbox"}
        assert len(await store.list_provider_allocations("fake:test")) == 1
        assert len(await store.expired_orphans(10, inventory_started_at=now)) == 1

        with pytest.raises(ProviderError) as fenced:
            await manager.ensure("sandbox", SandboxEnsureRequest())
        assert fenced.value.code == "provider_create_outcome_unknown"
        assert provider.create_count == 0

        provider.purge_visible = True
        await manager._purge_expired_orphan(orphan, provider.capacity_policy)
        assert provider.objects == {}
        assert await store.list_provider_allocations("fake:test") == []
        assert await store.expired_orphans(0, inventory_started_at=time.time()) == []

        assert await manager.delete("sandbox") is False
        assert await store.get_sandbox("sandbox") is None
        await manager.ensure("sandbox", SandboxEnsureRequest())
        assert provider.create_count == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_discovered_duplicate_allocation_cannot_protect_its_own_orphan(
    tmp_path,
    monkeypatch,
):
    provider = DuplicateInventoryProvider()
    manager, _, store = await _manager(tmp_path, provider)
    monkeypatch.setattr(settings, "agentbox_orphan_grace_seconds", 0)
    try:
        await manager.ensure("sandbox", SandboxEnsureRequest())
        record = await store.get_sandbox("sandbox")
        assert record is not None and record.provider_id == "provider-sandbox"
        provider.duplicates["provider-duplicate"] = "sandbox"

        await manager.reconcile()

        assert provider.objects == {"sandbox": "provider-sandbox"}
        assert provider.duplicates == {}
        assert provider.purged == [
            SandboxRef("sandbox", "provider-duplicate")
        ]
        allocations = await store.list_provider_allocations("fake:test")
        assert [row.provider_id for row in allocations] == ["provider-sandbox"]
        assert await store.expired_orphans(0, inventory_started_at=time.time()) == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_same_attempt_token_duplicate_retains_exact_orphan_id(tmp_path):
    provider = DuplicateInventoryProvider()
    manager, _, store = await _manager(tmp_path, provider)
    try:
        await manager.ensure("sandbox", SandboxEnsureRequest())
        [allocation] = [
            row
            for row in await store.list_provider_allocations("fake:test")
            if not row.allocation_id.startswith("provider:")
        ]
        provider.duplicates["provider-duplicate"] = "sandbox"
        provider.duplicate_tokens["provider-duplicate"] = allocation.allocation_id

        await manager.reconcile()

        orphans = await store.list_orphans(
            provider.provider_name,
            sandbox_id="sandbox",
        )
        assert [orphan.provider_id for orphan in orphans] == [
            "provider-duplicate"
        ]

        # A later eventual-consistency omission cannot lose the exact ID.
        original_list = provider.list_managed

        async def hide_duplicate():
            return [
                item
                for item in await original_list()
                if item.ref.provider_id != "provider-duplicate"
            ]

        provider.list_managed = hide_duplicate  # type: ignore[method-assign]
        assert await manager.delete("sandbox") is True
        assert SandboxRef("sandbox", "provider-duplicate") in provider.purged
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_durable_hidden_orphan_fences_create_until_exact_delete(tmp_path):
    provider = EventuallyConsistentPurgeProvider()
    manager, _, store = await _manager(tmp_path, provider)
    now = time.time()
    try:
        await store.observe_orphan(
            provider.provider_name,
            "provider-hidden",
            sandbox_id="sandbox",
            observed_at=now - 100,
        )

        with pytest.raises(ProviderError) as error:
            await manager.ensure("sandbox", SandboxEnsureRequest())

        assert error.value.code == "provider_generation_conflict"
        assert provider.create_count == 0

        provider.objects["sandbox"] = "provider-hidden"
        provider.hide_inventory = True
        provider.purge_visible = True
        assert await manager.delete("sandbox") is True
        assert provider.purge_attempts == [
            SandboxRef("sandbox", "provider-hidden")
        ]
        assert (
            await store.list_orphans(
                provider.provider_name,
                sandbox_id="sandbox",
            )
            == []
        )

        await manager.ensure("sandbox", SandboxEnsureRequest())
        assert provider.create_count == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_attempt_token_inventory_is_not_observed_as_orphan_while_claimed(
    tmp_path,
):
    provider = AmbiguousLifecycleProvider()
    manager, _, store = await _manager(tmp_path, provider)
    try:
        await store.insert_sandbox_if_missing("sandbox")
        allocation = await store.reserve_provider_allocation(
            "fake:test",
            "sandbox",
            owner="manager-a",
            max_active=10,
            ttl_seconds=60,
        )
        assert allocation is not None
        await store.hold_provider_allocation(
            "fake:test",
            allocation.allocation_id,
            owner="agentbox:outcome-unknown",
        )
        claim = await store.acquire_lifecycle_claim(
            "sandbox",
            operation="ensure",
            owner="other-manager",
            ttl_seconds=60,
        )
        assert claim is not None
        provider.objects["sandbox"] = "provider-sandbox"
        provider.generation_tokens["sandbox"] = allocation.allocation_id

        await manager.reconcile()

        assert (
            await store.list_orphans(
                provider.provider_name,
                sandbox_id="sandbox",
            )
            == []
        )
        assert provider.objects == {"sandbox": "provider-sandbox"}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_inventory_discovery_allocation_is_never_adopted_as_create_intent(
    tmp_path,
):
    provider = AmbiguousLifecycleProvider()
    manager, _, store = await _manager(tmp_path, provider)
    provider.objects["sandbox"] = "provider-discovered"
    try:
        await store.insert_sandbox_if_missing("sandbox")
        await store.reconcile_provider_allocations(
            "fake:test",
            {"provider-discovered": ("sandbox", None)},
            inventory_started_at=time.time(),
        )

        with pytest.raises(ProviderError) as error:
            await manager.ensure("sandbox", SandboxEnsureRequest())

        assert error.value.code == "provider_generation_conflict"
        assert provider.adopt_count == 0
        assert provider.create_count == 0
        record = await store.get_sandbox("sandbox")
        assert record is not None and record.provider_id is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_intent_generation_is_not_adopted_while_duplicate_is_discovered(
    tmp_path,
):
    provider = DuplicateInventoryProvider()
    manager, _, store = await _manager(tmp_path, provider)
    try:
        await store.insert_sandbox_if_missing("sandbox")
        allocation = await store.reserve_provider_allocation(
            "fake:test",
            "sandbox",
            owner="manager-a",
            max_active=10,
            ttl_seconds=60,
        )
        assert allocation is not None
        provider.objects["sandbox"] = "provider-intent"
        provider.generation_tokens["sandbox"] = allocation.allocation_id
        provider.duplicates["provider-duplicate"] = "sandbox"
        await store.reconcile_provider_allocations(
            "fake:test",
            {
                "provider-intent": ("sandbox", allocation.allocation_id),
                "provider-duplicate": ("sandbox", None),
            },
            inventory_started_at=time.time(),
        )

        with pytest.raises(ProviderError) as error:
            await manager.ensure("sandbox", SandboxEnsureRequest())

        assert error.value.code == "provider_generation_conflict"
        assert provider.adopt_count == 0
        assert provider.create_count == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_environment_replacement_purges_every_known_generation(tmp_path):
    provider = DuplicateInventoryProvider()
    manager, _, store = await _manager(tmp_path, provider)
    try:
        await manager.ensure(
            "sandbox",
            SandboxEnsureRequest(env={"LEMMA_BASE_URL": "https://old.example"}),
        )
        provider.duplicates["provider-duplicate"] = "sandbox"

        status = await manager.ensure(
            "sandbox",
            SandboxEnsureRequest(env={"LEMMA_BASE_URL": "https://new.example"}),
        )

        assert status.ready is True
        assert provider.duplicates == {}
        assert set(provider.purged) == {
            SandboxRef("sandbox", "provider-sandbox"),
            SandboxRef("sandbox", "provider-duplicate"),
        }
        assert provider.create_count == 2
        record = await store.get_sandbox("sandbox")
        assert record is not None
        assert record.env["LEMMA_BASE_URL"] == "https://new.example"
        allocations = await store.list_provider_allocations("fake:test")
        assert len(allocations) == 1
        assert not allocations[0].allocation_id.startswith("provider:")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_delete_purges_every_generation_with_same_attempt_token(tmp_path):
    provider = DuplicateInventoryProvider()
    manager, _, store = await _manager(tmp_path, provider)
    try:
        await store.insert_sandbox_if_missing("sandbox")
        allocation = await store.reserve_provider_allocation(
            "fake:test",
            "sandbox",
            owner="manager-a",
            max_active=10,
            ttl_seconds=60,
        )
        assert allocation is not None
        held = await store.hold_provider_allocation(
            "fake:test",
            allocation.allocation_id,
            owner="agentbox:outcome-unknown",
        )
        assert held is not None
        for provider_id in ("provider-duplicate-a", "provider-duplicate-b"):
            provider.duplicates[provider_id] = "sandbox"
            provider.duplicate_tokens[provider_id] = allocation.allocation_id

        assert await manager.delete("sandbox") is True

        assert provider.duplicates == {}
        assert set(provider.purged) == {
            SandboxRef("sandbox", "provider-duplicate-a"),
            SandboxRef("sandbox", "provider-duplicate-b"),
        }
        assert await store.list_provider_allocations("fake:test") == []
        assert await store.get_sandbox("sandbox") is None

        await manager.ensure("sandbox", SandboxEnsureRequest())
        assert provider.create_count == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_concurrent_managers_create_only_one_provider_generation(tmp_path):
    provider = AmbiguousLifecycleProvider()
    manager_a, _, store = await _manager(tmp_path, provider)
    manager_b = SandboxLifecycleManager(provider, store, owner="manager-b")
    manager_b._wait_until_runtime_ready = manager_a._wait_until_runtime_ready  # type: ignore[method-assign]
    try:
        statuses = await asyncio.gather(
            manager_a.ensure("sandbox", SandboxEnsureRequest()),
            manager_b.ensure("sandbox", SandboxEnsureRequest()),
        )
        assert all(status.ready for status in statuses)
        assert provider.create_count == 1
        assert provider.adopt_count == 1
        assert set(provider.objects) == {"sandbox"}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_session_delete_waits_for_active_activity_lease(tmp_path):
    manager, provider, store = await _manager(tmp_path)
    try:
        await manager.ensure("sandbox", SandboxEnsureRequest())
        await store.upsert_session(
            "sandbox",
            "session",
            cwd="/workspace/c/session",
            env_keys=[],
        )
        lease = await store.acquire_activity_lease(
            "sandbox",
            session_id="session",
            operation="test",
            owner="operation-owner",
            ttl_seconds=60,
        )
        assert lease is not None

        with pytest.raises(ProviderError) as busy:
            await manager.delete_session("sandbox", "session")
        assert busy.value.code == "session_busy"
        assert provider.deleted_sessions == []

        assert await store.release_activity_lease(
            lease.lease_id,
            owner="operation-owner",
        )
        assert await manager.delete_session("sandbox", "session") is True
        assert provider.deleted_sessions == [("sandbox", "session")]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cancelled_create_is_recorded_before_cancellation_propagates(tmp_path):
    manager, provider, store = await _manager(tmp_path)
    try:
        provider.allow_create.clear()
        task = asyncio.create_task(manager.ensure("sandbox", SandboxEnsureRequest()))
        await provider.create_started.wait()
        task.cancel()
        provider.allow_create.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        record = await store.get_sandbox("sandbox")
        assert record is not None
        assert record.provider_id == "provider-sandbox"
        assert record.observed_generation == record.desired_generation
        allocations = await store.list_provider_allocations("fake:test")
        assert [(row.state, row.provider_id) for row in allocations] == [
            ("active", "provider-sandbox")
        ]
        assert provider.requests["sandbox"] == {
            "AGENTBOX_FUNCTION_MAX_CONCURRENCY": "8",
            "AGENTBOX_FUNCTION_MAX_QUEUED": "32",
        }
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_suspend_resume_preserves_identity_and_delete_purges(tmp_path):
    manager, provider, store = await _manager(tmp_path)
    try:
        await manager.ensure("sandbox", SandboxEnsureRequest())
        provider_id = (await store.get_sandbox("sandbox")).provider_id
        assert await manager.suspend("sandbox") is True
        suspended = await store.get_sandbox("sandbox")
        assert suspended is not None and suspended.desired_state == "suspended"
        assert suspended.idle_since_at is not None
        assert provider.objects["sandbox"] == provider_id

        await manager.ensure("sandbox", SandboxEnsureRequest())
        resumed = await store.get_sandbox("sandbox")
        assert resumed is not None and resumed.desired_state == "present"
        assert resumed.provider_id == provider_id

        assert await manager.delete("sandbox") is True
        assert await store.get_sandbox("sandbox") is None
        assert "sandbox" not in provider.objects
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_explicit_delete_invokes_optional_storage_purge_only_at_delete(tmp_path):
    provider = StorageLifecycleProvider()
    manager, _, store = await _manager(tmp_path, provider)
    try:
        await manager.ensure("sandbox", SandboxEnsureRequest())
        await manager.suspend("sandbox")
        await manager.ensure("sandbox", SandboxEnsureRequest())
        assert provider.storage == {"sandbox"}
        assert provider.storage_purges == []

        assert await manager.delete("sandbox") is True
        assert provider.storage == set()
        assert provider.storage_purges == ["sandbox"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_absent_delete_cannot_purge_concurrent_ensure_storage(tmp_path):
    provider = BlockingStoragePurgeProvider()
    manager, _, store = await _manager(tmp_path, provider)
    try:
        deleting = asyncio.create_task(manager.delete("sandbox"))
        await provider.purge_started.wait()

        ensuring = asyncio.create_task(
            manager.ensure("sandbox", SandboxEnsureRequest())
        )
        await asyncio.sleep(0.05)
        assert provider.create_count == 0
        assert not ensuring.done()

        provider.allow_purge.set()
        assert await deleting is False
        status = await ensuring

        assert status.ready is True
        assert provider.storage == {"sandbox"}
        assert provider.storage_purges == ["sandbox"]
        assert provider.create_count == 1
        record = await store.get_sandbox("sandbox")
        assert record is not None and record.desired_state == "present"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_durable_environment_rebuild_preserves_provider_storage(tmp_path):
    provider = StorageLifecycleProvider()
    manager, _, store = await _manager(tmp_path, provider)
    try:
        await manager.ensure(
            "sandbox",
            SandboxEnsureRequest(env={"LEMMA_BASE_URL": "https://one.example"}),
        )
        await manager.ensure(
            "sandbox",
            SandboxEnsureRequest(env={"LEMMA_BASE_URL": "https://two.example"}),
        )

        assert provider.delete_count == 1
        assert provider.create_count == 2
        assert provider.storage == {"sandbox"}
        assert provider.storage_purges == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_authenticated_proxy_revisit_resumes_retained_provider_object(tmp_path):
    manager, provider, store = await _manager(tmp_path)
    try:
        await manager.ensure("sandbox", SandboxEnsureRequest())
        provider_id = (await store.get_sandbox("sandbox")).provider_id
        await manager.suspend("sandbox")

        async with activity_lease(
            store,
            manager,
            "sandbox",
            session_id=None,
            operation="public-http:browser",
        ):
            record_during_access = await store.get_sandbox("sandbox")
            assert record_during_access is not None
            assert record_during_access.desired_state == "present"

        record = await store.get_sandbox("sandbox")
        assert record is not None and record.desired_state == "present"
        assert record.provider_id == provider_id
        assert provider.create_count == 2
        assert provider.objects["sandbox"] == provider_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_suspended_retention_is_measured_from_suspension(tmp_path, monkeypatch):
    manager, provider, store = await _manager(tmp_path)
    try:
        await manager.ensure("sandbox", SandboxEnsureRequest())
        await manager.suspend("sandbox")
        with store._store._lock, store._store._conn:
            store._store._conn.execute(
                "UPDATE sandboxes SET idle_since_at = ? WHERE sandbox_id = ?",
                (time.time() - 11, "sandbox"),
            )
        monkeypatch.setattr(settings, "agentbox_suspended_retention_seconds", 10.0)
        await manager.reconcile()
        assert await store.get_sandbox("sandbox") is None
        assert "sandbox" not in provider.objects
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_lost_activity_lease_cancels_owner(monkeypatch):
    class LostStore:
        async def renew_activity_lease(self, *args, **kwargs):
            del args, kwargs
            return None

    monkeypatch.setattr(settings, "agentbox_activity_lease_ttl_seconds", 0.03)
    owner = asyncio.create_task(asyncio.sleep(60))
    await _renew_activity_lease(  # type: ignore[arg-type]
        LostStore(),
        type("Manager", (), {"owner": "manager-a"})(),
        "lease",
        owner,
    )
    with pytest.raises(asyncio.CancelledError):
        await owner


@pytest.mark.asyncio
async def test_missing_and_suspended_heartbeats_fail_without_claim_wait(tmp_path):
    manager, provider, store = await _manager(tmp_path)
    del provider
    try:
        started = time.monotonic()
        assert await manager.heartbeat_sandbox("missing") is False
        assert await manager.heartbeat_session("missing", "session") is False
        assert time.monotonic() - started < 0.2

        await manager.ensure("sandbox", SandboxEnsureRequest())
        await store.upsert_session("sandbox", "session", cwd="/workspace", env_keys=[])
        assert await manager.heartbeat_sandbox("sandbox") is True
        assert await manager.heartbeat_session("sandbox", "session") is True
        await manager.suspend("sandbox")
        assert await manager.heartbeat_sandbox("sandbox") is False
        assert await manager.heartbeat_session("sandbox", "session") is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_heartbeats_renew_optional_provider_lease(tmp_path):
    provider = LeaseLifecycleProvider()
    manager, _, store = await _manager(tmp_path, provider)
    try:
        await manager.ensure("sandbox", SandboxEnsureRequest())
        await store.upsert_session("sandbox", "session", cwd="/workspace", env_keys=[])

        assert await manager.heartbeat_sandbox("sandbox") is True
        assert await manager.heartbeat_session("sandbox", "session") is True
        assert provider.lease_renewals == ["sandbox", "sandbox"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_heartbeats_do_not_require_provider_lease_hook(tmp_path):
    manager, provider, store = await _manager(tmp_path)
    assert not hasattr(provider, "renew_lease")
    try:
        await manager.ensure("sandbox", SandboxEnsureRequest())
        assert await manager.heartbeat_sandbox("sandbox") is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_activity_lease_renews_provider_immediately(tmp_path):
    provider = LeaseLifecycleProvider()
    manager, _, store = await _manager(tmp_path, provider)
    try:
        await manager.ensure("sandbox", SandboxEnsureRequest())
        async with activity_lease(
            store,
            manager,
            "sandbox",
            session_id=None,
            operation="test",
        ):
            assert provider.lease_renewals == ["sandbox"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_activity_after_manager_restart_adopts_exact_provider_id(tmp_path):
    provider = AmbiguousLifecycleProvider()
    manager, _, store = await _manager(tmp_path, provider)
    try:
        await manager.ensure("sandbox", SandboxEnsureRequest())
        record = await store.get_sandbox("sandbox")
        assert record is not None
        provider_id = record.provider_id

        restarted = SandboxLifecycleManager(
            provider,
            store,
            owner="manager-after-restart",
        )
        async with activity_lease(
            store,
            restarted,
            "sandbox",
            session_id=None,
            operation="test-after-restart",
        ):
            assert provider.adopt_count == 1
            assert provider.objects["sandbox"] == provider_id

        assert provider.create_count == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_activity_lease_retries_after_provider_renewal_failure(monkeypatch):
    renewed_twice = asyncio.Event()

    class RenewingStore:
        calls = 0

        async def renew_activity_lease(self, lease_id, **kwargs):
            del lease_id, kwargs
            self.calls += 1
            if self.calls >= 2:
                renewed_twice.set()
            return ActivityLease(
                lease_id="lease",
                sandbox_id="sandbox",
                session_id=None,
                operation="test",
                owner="manager-a",
                expires_at=time.time() + 60,
            )

    class RenewingManager:
        owner = "manager-a"
        calls = 0

        async def renew_provider_lease(self, sandbox_id):
            assert sandbox_id == "sandbox"
            self.calls += 1
            if self.calls == 1:
                raise ProviderError("temporary", retryable=True)

    real_sleep = asyncio.sleep

    async def fast_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr(lifecycle_module.asyncio, "sleep", fast_sleep)
    manager = RenewingManager()
    renewal = asyncio.create_task(
        _renew_activity_lease(
            RenewingStore(),  # type: ignore[arg-type]
            manager,  # type: ignore[arg-type]
            "lease",
            None,
        )
    )
    await asyncio.wait_for(renewed_twice.wait(), timeout=1)
    renewal.cancel()
    with pytest.raises(asyncio.CancelledError):
        await renewal
    assert manager.calls >= 2


@pytest.mark.asyncio
async def test_restart_does_not_recreate_long_idle_missing_compute(tmp_path):
    manager, provider, store = await _manager(tmp_path)
    try:
        await manager.ensure("sandbox", SandboxEnsureRequest())
        creates = provider.create_count
        provider.objects.clear()
        with store._store._lock, store._store._conn:
            store._store._conn.execute(
                """
                UPDATE sandboxes SET idle_since_at = NULL, last_active_at = ?
                WHERE sandbox_id = ?
                """,
                (
                    time.time() - settings.agentbox_sandbox_idle_timeout_seconds - 10,
                    "sandbox",
                ),
            )

        await manager.reconcile()

        record = await store.get_sandbox("sandbox")
        assert record is not None and record.desired_state == "suspended"
        assert provider.create_count == creates
    finally:
        await store.close()
