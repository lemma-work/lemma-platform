from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from agentbox.domain import (
    AdmissionClass,
    AgentBoxError,
    ErrorCode,
    ProviderAdmissionPolicy,
    SandboxKey,
    SandboxProfileRef,
    StorageKind,
    WorkloadKind,
)
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.persistence.uow import StateDatabase
from agentbox.ports import (
    ProviderAllocationRef,
    ProviderCreateRequest,
    ProviderCreateResult,
    ProviderRateLimited,
    ProviderReadyResult,
)


pytestmark = pytest.mark.asyncio


def profile() -> SandboxProfileRef:
    return SandboxProfileRef("function-python-v1", f"sha256:{'b' * 64}")


@pytest_asyncio.fixture
async def database(tmp_path: Path):
    state = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await state.create_schema_for_test()
    try:
        yield state
    finally:
        await state.dispose()


class AdmissionProvider:
    name = "admission"
    scope = "admission:test"
    workspace_storage_kind = StorageKind.VOLUME

    def __init__(self, database: StateDatabase) -> None:
        self.database = database
        self.create_calls = 0
        self.destroy_calls = 0
        self.rate_limit_first = False

    async def create(self, request: ProviderCreateRequest) -> ProviderCreateResult:
        assert self.database.active_units_of_work == 0
        self.create_calls += 1
        if self.rate_limit_first and self.create_calls == 1:
            raise ProviderRateLimited("provider 429", retry_after_ms=5_000)
        provider_id = f"allocation-{request.allocation_id}"
        return ProviderCreateResult(
            provider_id=provider_id,
            provider_instance_id=provider_id,
            provider_request_id=None,
            workspace_storage=None,
        )

    async def wait_ready(
        self,
        allocation: ProviderAllocationRef,
        *,
        profile: SandboxProfileRef,
        deadline_at: datetime,
    ) -> ProviderReadyResult:
        del profile, deadline_at
        assert self.database.active_units_of_work == 0
        return ProviderReadyResult(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_instance_id,
        )

    async def destroy_allocation(
        self, allocation: ProviderAllocationRef, *, deadline_at: datetime
    ) -> None:
        del allocation, deadline_at
        assert self.database.active_units_of_work == 0
        self.destroy_calls += 1

    async def close(self) -> None:
        return None


def service(
    database: StateDatabase,
    provider: AdmissionProvider,
    *,
    max_active: int,
    rate: float = 100,
    burst: int = 100,
    interactive_reserve: int = 0,
    latency_reserve: int = 0,
) -> SandboxLifecycleService:
    return SandboxLifecycleService(
        database,
        provider,
        ProviderAdmissionPolicy(
            max_active=max_active,
            create_rate_per_second=rate,
            create_burst=burst,
            interactive_capacity_reserve=interactive_reserve,
            latency_capacity_reserve=latency_reserve,
        ),
    )


async def ensure(
    lifecycle: SandboxLifecycleService,
    admission_class: AdmissionClass,
) -> SandboxKey:
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    await lifecycle.ensure(
        key,
        profile(),
        admission_class=admission_class,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    return key


async def assert_rejected(
    lifecycle: SandboxLifecycleService,
    admission_class: AdmissionClass,
    code: ErrorCode,
) -> None:
    with pytest.raises(AgentBoxError) as raised:
        await ensure(lifecycle, admission_class)
    assert raised.value.code == code


async def test_class_reserves_protect_latency_and_interactive_capacity(
    database: StateDatabase,
) -> None:
    provider = AdmissionProvider(database)
    lifecycle = service(
        database,
        provider,
        max_active=4,
        interactive_reserve=1,
        latency_reserve=1,
    )

    await ensure(lifecycle, AdmissionClass.BATCH)
    await ensure(lifecycle, AdmissionClass.BATCH)
    await assert_rejected(lifecycle, AdmissionClass.BATCH, ErrorCode.CAPACITY_EXHAUSTED)
    await ensure(lifecycle, AdmissionClass.LATENCY)
    await assert_rejected(
        lifecycle, AdmissionClass.LATENCY, ErrorCode.CAPACITY_EXHAUSTED
    )
    await ensure(lifecycle, AdmissionClass.INTERACTIVE)
    await assert_rejected(
        lifecycle, AdmissionClass.INTERACTIVE, ErrorCode.CAPACITY_EXHAUSTED
    )

    assert provider.create_calls == 4


async def test_internal_token_bucket_stops_create_before_provider_io(
    database: StateDatabase,
) -> None:
    provider = AdmissionProvider(database)
    lifecycle = service(database, provider, max_active=4, rate=0.01, burst=1)

    await ensure(lifecycle, AdmissionClass.INTERACTIVE)
    await assert_rejected(lifecycle, AdmissionClass.INTERACTIVE, ErrorCode.RATE_LIMITED)

    assert provider.create_calls == 1


async def test_provider_429_opens_shared_create_circuit(
    database: StateDatabase,
) -> None:
    provider = AdmissionProvider(database)
    provider.rate_limit_first = True
    lifecycle = service(database, provider, max_active=4)

    await assert_rejected(lifecycle, AdmissionClass.INTERACTIVE, ErrorCode.RATE_LIMITED)
    await assert_rejected(lifecycle, AdmissionClass.INTERACTIVE, ErrorCode.RATE_LIMITED)

    assert provider.create_calls == 1


async def test_destroy_releases_distributed_capacity(
    database: StateDatabase,
) -> None:
    provider = AdmissionProvider(database)
    lifecycle = service(database, provider, max_active=1)
    first = await ensure(lifecycle, AdmissionClass.LATENCY)
    await assert_rejected(
        lifecycle, AdmissionClass.LATENCY, ErrorCode.CAPACITY_EXHAUSTED
    )

    await lifecycle.destroy(
        first,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    await ensure(lifecycle, AdmissionClass.LATENCY)

    assert provider.create_calls == 2
    assert provider.destroy_calls == 1
