from __future__ import annotations

import asyncio
import time

import pytest

from agentbox.api.lifecycle import _renew_activity_lease, activity_lease
from agentbox.config import settings
from agentbox.lifecycle_manager import SandboxLifecycleManager
from agentbox.providers.models import (
    ManagedSandbox,
    ProviderCapacityPolicy,
    SandboxEndpoint,
    SandboxRef,
)
from agentbox.schemas import SandboxEnsureRequest, SandboxInternalStatus
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

    @property
    def capacity_policy(self) -> ProviderCapacityPolicy:
        return ProviderCapacityPolicy(scope="fake:test", max_active=10)

    async def create(self, sandbox_id, request):
        self.requests[sandbox_id] = dict(request.env)
        self.create_started.set()
        await self.allow_create.wait()
        self.create_count += 1
        self.objects.setdefault(sandbox_id, f"provider-{sandbox_id}")
        return self._status(sandbox_id, ready=True)

    async def get_status(self, sandbox_id):
        return self._status(sandbox_id, ready=sandbox_id in self.objects)

    async def list_managed(self):
        return [
            ManagedSandbox(
                ref=SandboxRef(sandbox_id=sandbox_id, provider_id=provider_id),
                status=self._status(sandbox_id, ready=True),
                instance_id=provider_id,
            )
            for sandbox_id, provider_id in self.objects.items()
        ]

    async def resolve_endpoint(self, sandbox_id, app, *, protocol="http"):
        del app, protocol
        return SandboxEndpoint(
            base_url="http://runtime.test",
            instance_id=self.objects.get(sandbox_id),
        )

    async def release(self, sandbox_id):
        if sandbox_id not in self.objects:
            return False
        self.release_count += 1
        return True

    async def delete(self, sandbox_id):
        self.delete_count += 1
        return self.objects.pop(sandbox_id, None) is not None

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


async def _manager(tmp_path):
    store = await SQLiteStateStore.open(
        str(tmp_path / "state.db"),
        durable_env_keys=settings.agentbox_state_durable_env_key_set,
    )
    provider = FakeLifecycleProvider()
    manager = SandboxLifecycleManager(provider, store, owner="manager-a")

    async def ready(sandbox_id: str):
        return SandboxEndpoint(
            base_url="http://runtime.test",
            instance_id=provider.objects.get(sandbox_id),
        )

    manager._wait_until_runtime_ready = ready  # type: ignore[method-assign]
    return manager, provider, store


@pytest.mark.asyncio
async def test_cancelled_create_is_recorded_before_cancellation_propagates(tmp_path):
    manager, provider, store = await _manager(tmp_path)
    try:
        provider.allow_create.clear()
        task = asyncio.create_task(
            manager.ensure("sandbox", SandboxEnsureRequest())
        )
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
        await store.upsert_session(
            "sandbox", "session", cwd="/workspace", env_keys=[]
        )
        assert await manager.heartbeat_sandbox("sandbox") is True
        assert await manager.heartbeat_session("sandbox", "session") is True
        await manager.suspend("sandbox")
        assert await manager.heartbeat_sandbox("sandbox") is False
        assert await manager.heartbeat_session("sandbox", "session") is False
    finally:
        await store.close()


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
                (time.time() - settings.agentbox_sandbox_idle_timeout_seconds - 10,
                 "sandbox"),
            )

        await manager.reconcile()

        record = await store.get_sandbox("sandbox")
        assert record is not None and record.desired_state == "suspended"
        assert provider.create_count == creates
    finally:
        await store.close()
