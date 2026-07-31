from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from agentbox.domain import (
    AdmissionClass,
    AgentBoxError,
    PortProtocol,
    SandboxKey,
    SandboxProfileRef,
    StorageKind,
    WorkloadKind,
)
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.persistence.uow import StateDatabase
from agentbox.port_access import PortAccessService, PortAccessSigner
from agentbox.ports import (
    ProviderAllocationRef,
    ProviderCreateRequest,
    ProviderCreateResult,
    ProviderMetadataEntry,
    ProviderPortTarget,
    ProviderReadyResult,
    ProviderStorageResult,
)


pytestmark = pytest.mark.asyncio


class Provider:
    name = "fake"
    scope = "fake:test"
    workspace_storage_kind = StorageKind.VOLUME

    def __init__(self, database: StateDatabase) -> None:
        self.database = database
        self.resolved: list[tuple[str, int, PortProtocol]] = []
        self.activity_until: list[datetime | None] = []

    async def create(self, request: ProviderCreateRequest) -> ProviderCreateResult:
        assert self.database.active_units_of_work == 0
        storage = request.workspace_storage
        return ProviderCreateResult(
            provider_id=f"sandbox-{request.allocation_id}",
            provider_instance_id=None,
            provider_request_id=None,
            workspace_storage=(
                ProviderStorageResult(
                    provider_storage_id=f"volume-{storage.storage_token}",
                    bound_to_allocation=False,
                )
                if storage is not None
                else None
            ),
        )

    async def wait_ready(self, allocation: ProviderAllocationRef, **_kwargs):
        assert self.database.active_units_of_work == 0
        return ProviderReadyResult(
            provider_id=allocation.provider_id,
            provider_instance_id=None,
        )

    async def resolve_port_target(
        self,
        allocation: ProviderAllocationRef,
        *,
        port: int,
        protocol: PortProtocol,
        deadline_at: datetime,
        activity_until: datetime | None = None,
    ) -> ProviderPortTarget:
        del deadline_at
        assert self.database.active_units_of_work == 0
        self.resolved.append((allocation.provider_id, port, protocol))
        self.activity_until.append(activity_until)
        return ProviderPortTarget(
            base_url=f"{protocol.value}://127.0.0.1:{port}",
            headers=(
                ProviderMetadataEntry(
                    name="X-Provider-Access-Token",
                    value="provider-secret",
                ),
            ),
        )


@pytest_asyncio.fixture
async def database(tmp_path: Path):
    state = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await state.create_schema_for_test()
    try:
        yield state
    finally:
        await state.dispose()


async def test_signed_grant_resolves_exact_current_allocation_outside_uow(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    lifecycle = SandboxLifecycleService(database, provider)
    handle = await lifecycle.ensure(
        key,
        SandboxProfileRef("workspace-python-v1", f"sha256:{'a' * 64}"),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )
    signer = PortAccessSigner(b"p" * 32)
    service = PortAccessService(
        database,
        provider,
        signer,
        public_base_url="https://agentbox.example",
    )
    grant = await service.create(
        key,
        port=4848,
        protocol=PortProtocol.HTTP,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    token = grant.url.split("/port-access/", 1)[1].rstrip("/")

    claims, target = await service.resolve(token, deadline_at=deadline)

    assert handle.ready is True
    assert claims.allocation_id == handle.allocation_id
    assert claims.allocation_epoch == handle.allocation_epoch
    assert target.base_url == "http://127.0.0.1:4848"
    assert provider.resolved[0][1:] == (4848, PortProtocol.HTTP)
    assert database.active_units_of_work == 0


async def test_tampered_and_stale_grants_fail_closed(database: StateDatabase) -> None:
    provider = Provider(database)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    lifecycle = SandboxLifecycleService(database, provider)
    await lifecycle.ensure(
        key,
        SandboxProfileRef("workspace-python-v1", f"sha256:{'a' * 64}"),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )
    service = PortAccessService(
        database,
        provider,
        PortAccessSigner(b"q" * 32),
        public_base_url="https://agentbox.example",
    )
    grant = await service.create(
        key,
        port=4848,
        protocol=PortProtocol.HTTP,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    token = grant.url.split("/port-access/", 1)[1].rstrip("/")

    with pytest.raises(AgentBoxError) as tampered:
        await service.resolve(token[:-1] + "x", deadline_at=deadline)
    async with database.uow() as uow:
        await uow.repository.begin_release(
            key,
            claimed_until=deadline,
            retention_seconds=7 * 24 * 60 * 60,
        )
        await uow.commit()
    with pytest.raises(AgentBoxError) as stale:
        await service.resolve(token, deadline_at=deadline)

    assert tampered.value.status_code == 403
    assert stale.value.status_code == 410
    assert provider.resolved == []


async def test_function_port_grant_protects_long_invocation_from_idle_destroy(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    now = datetime.now(timezone.utc)
    lifecycle = SandboxLifecycleService(database, provider)
    await lifecycle.ensure(
        key,
        SandboxProfileRef("function-python-v1", f"sha256:{'b' * 64}"),
        admission_class=AdmissionClass.LATENCY,
        deadline_at=now + timedelta(seconds=30),
    )
    service = PortAccessService(
        database,
        provider,
        PortAccessSigner(b"r" * 32),
        public_base_url="https://agentbox.example",
    )
    protected_until = now + timedelta(minutes=10)
    with pytest.raises(AgentBoxError) as unsupported:
        await service.create(
            key,
            port=8080,
            protocol=PortProtocol.HTTP,
            expires_at=protected_until,
        )
    assert unsupported.value.code.value == "UNSUPPORTED_CAPABILITY"
    await service.create(
        key,
        port=8090,
        protocol=PortProtocol.HTTP,
        expires_at=protected_until,
    )

    async with database.uow() as uow:
        logical = await uow.repository.get_logical(key)
        protected_claims = await uow.repository.claim_due_maintenance(
            workspace_idle_before=now + timedelta(minutes=1),
            function_idle_before=now + timedelta(minutes=1),
            claimed_until=now + timedelta(minutes=7),
            now=now + timedelta(minutes=6),
        )
        await uow.commit()

    assert logical is not None
    assert logical.protected_until == protected_until
    assert protected_claims == ()


async def test_function_runtime_grant_allows_long_job_but_workspace_does_not(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    now = datetime.now(timezone.utc)
    lifecycle = SandboxLifecycleService(database, provider)
    service = PortAccessService(
        database,
        provider,
        PortAccessSigner(b"s" * 32),
        public_base_url="https://agentbox.example",
    )
    function_key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    await lifecycle.ensure(
        function_key,
        SandboxProfileRef("function-python-v1", f"sha256:{'c' * 64}"),
        admission_class=AdmissionClass.BATCH,
        deadline_at=now + timedelta(seconds=30),
    )
    grant = await service.create(
        function_key,
        port=8090,
        protocol=PortProtocol.HTTP,
        expires_at=now + timedelta(hours=23),
    )
    assert grant.expires_at == now + timedelta(hours=23)

    workspace_key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    await lifecycle.ensure(
        workspace_key,
        SandboxProfileRef("workspace-python-v1", f"sha256:{'d' * 64}"),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=now + timedelta(seconds=30),
    )
    with pytest.raises(AgentBoxError, match="cannot exceed one hour"):
        await service.create(
            workspace_key,
            port=4848,
            protocol=PortProtocol.HTTP,
            expires_at=now + timedelta(hours=2),
        )


async def test_function_runtime_lease_returns_direct_allocation_fenced_target(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    profile = SandboxProfileRef("function-python-v1", f"sha256:{'e' * 64}")
    now = datetime.now(timezone.utc)
    lifecycle = SandboxLifecycleService(database, provider)
    handle = await lifecycle.ensure(
        key,
        profile,
        admission_class=AdmissionClass.LATENCY,
        deadline_at=now + timedelta(seconds=30),
    )
    required_valid_until = now + timedelta(minutes=10)
    service = PortAccessService(
        database,
        provider,
        PortAccessSigner(b"t" * 32),
        public_base_url="https://agentbox.example",
        trusted_function_activity_seconds=300,
        trusted_function_activity_refresh_seconds=60,
    )

    lease = await service.lease_function_runtime(
        key.logical_id,
        deadline_at=now + timedelta(seconds=20),
        required_valid_until=required_valid_until,
    )

    assert lease.key == key
    assert lease.allocation_id == handle.allocation_id
    assert lease.allocation_epoch == handle.allocation_epoch
    assert lease.profile == profile
    assert lease.url == "http://127.0.0.1:8090/"
    assert lease.expires_at == required_valid_until + timedelta(seconds=60)
    assert lease.request_headers[0].name == "X-Provider-Access-Token"
    assert "provider-secret" not in repr(lease)
    assert provider.resolved == [
        (f"sandbox-{handle.allocation_id}", 8090, PortProtocol.HTTP)
    ]
    assert provider.activity_until == [lease.expires_at]
    async with database.uow() as uow:
        logical = await uow.repository.get_logical(key)
        await uow.commit()
    assert logical is not None
    assert logical.protected_until == lease.expires_at


async def test_function_runtime_lease_requires_current_active_allocation(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    service = PortAccessService(
        database,
        provider,
        PortAccessSigner(b"u" * 32),
        public_base_url="https://agentbox.example",
    )

    with pytest.raises(AgentBoxError) as missing:
        await service.lease_function_runtime(
            key.logical_id,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=20),
            required_valid_until=datetime.now(timezone.utc) + timedelta(minutes=2),
        )

    assert missing.value.status_code == 404
    assert provider.resolved == []
