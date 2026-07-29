from __future__ import annotations

import asyncio
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
        self.resolved_activity_until: list[datetime | None] = []

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
        self.resolved_activity_until.append(activity_until)
        return ProviderPortTarget(base_url=f"{protocol.value}://127.0.0.1:{port}")


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


async def test_function_signed_port_grants_are_not_supported(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    service = PortAccessService(
        database,
        provider,
        PortAccessSigner(b"r" * 32),
        public_base_url="https://agentbox.example",
    )
    with pytest.raises(AgentBoxError) as unsupported:
        await service.create(
            key,
            port=8090,
            protocol=PortProtocol.HTTP,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
    assert unsupported.value.code.value == "UNSUPPORTED_CAPABILITY"


async def test_trusted_function_route_uses_current_allocation_and_refreshes_activity(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    lifecycle = SandboxLifecycleService(database, provider)
    await lifecycle.ensure(
        key,
        SandboxProfileRef("function-python-v1", f"sha256:{'e' * 64}"),
        admission_class=AdmissionClass.LATENCY,
        deadline_at=deadline,
    )
    service = PortAccessService(
        database,
        provider,
        PortAccessSigner(b"t" * 32),
        public_base_url="https://agentbox.example",
        trusted_function_activity_seconds=120,
    )
    before = datetime.now(timezone.utc)
    requested_activity_until = before + timedelta(minutes=10)

    targets = await asyncio.gather(
        *(
            service.resolve_trusted_function(
                key.logical_id,
                deadline_at=deadline,
                activity_until=requested_activity_until,
            )
            for _ in range(20)
        )
    )

    async with database.uow() as uow:
        logical = await uow.repository.get_logical(key)
        await uow.commit()
    assert all(target.base_url == "http://127.0.0.1:8090" for target in targets)
    assert len(provider.resolved) == 20
    assert provider.resolved[0][1:] == (8090, PortProtocol.HTTP)
    assert provider.resolved_activity_until[0] is not None
    assert provider.resolved_activity_until[0] == requested_activity_until + timedelta(
        seconds=60
    )
    assert logical is not None
    assert logical.protected_until == provider.resolved_activity_until[0]


async def test_workspace_port_grant_cannot_exceed_one_hour(
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
