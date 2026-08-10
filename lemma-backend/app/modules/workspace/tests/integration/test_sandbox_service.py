"""The sandbox state machine, against a real database and a fake provider.

The database is real because the epoch and storage-generation rules are
expressed as row updates, and a fake repository would only prove the fake.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from sandbox_runtime.errors import SandboxNotFound, SandboxRejected
from app.modules.workspace.domain.sandbox import (
    SandboxCapability,
    SandboxDesiredState,
    SandboxKind,
    SandboxOwnerKind,
)
from app.modules.workspace.infrastructure.sandbox_repository import SandboxRepository
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import (
    ProcessDescriptor,
    ProviderCreateSpec,
    ProviderInstance,
    ProviderRejected,
)
from app.modules.workspace.services.sandbox_service import SandboxService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@dataclass
class FakeProvider:
    name: str = "fake"
    containers: dict[str, ProviderInstance] = field(default_factory=dict)
    volumes: dict[UUID, str] = field(default_factory=dict)
    created: list[ProviderCreateSpec] = field(default_factory=list)
    released: list[str] = field(default_factory=list)
    destroyed: list[str] = field(default_factory=list)
    destroyed_volumes: list[str] = field(default_factory=list)
    fail_create: Exception | None = None
    # What `list_processes` reports; a None exit code means still running.
    processes: list[ProcessDescriptor] = field(default_factory=list)

    async def list_processes(self, instance, *, deadline_at):
        del instance, deadline_at
        return tuple(self.processes)

    async def create(self, spec: ProviderCreateSpec) -> ProviderInstance:
        if self.fail_create is not None:
            raise self.fail_create
        self.created.append(spec)
        instance = ProviderInstance(
            provider_id=spec.name,
            name=spec.name,
            volume_name=spec.volume_name,
            running=False,
        )
        self.containers[spec.name] = instance
        return instance

    async def wait_ready(self, instance, *, kind, deadline_at) -> None:
        self.containers[instance.name] = ProviderInstance(
            provider_id=instance.provider_id,
            name=instance.name,
            volume_name=instance.volume_name,
            running=True,
        )

    async def inspect(self, name: str, *, deadline_at) -> ProviderInstance | None:
        return self.containers.get(name)

    async def release(self, instance, *, kind, deadline_at) -> None:
        self.released.append(instance.name)
        self.containers.pop(instance.name, None)

    async def destroy(self, name: str, *, deadline_at) -> None:
        self.destroyed.append(name)
        self.containers.pop(name, None)

    async def find_volume(self, *, sandbox_id: UUID, deadline_at) -> str | None:
        return self.volumes.get(sandbox_id)

    async def ensure_volume(self, *, sandbox_id: UUID, name: str, deadline_at) -> str:
        self.volumes[sandbox_id] = name
        return name

    async def destroy_volume(self, name: str, *, deadline_at) -> None:
        self.destroyed_volumes.append(name)

    async def list_objects(self, *, deadline_at):
        return ()

    async def close(self) -> None:
        return None


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def service(provider: FakeProvider, sandbox_uow_factory) -> SandboxService:
    SandboxService._inflight.clear()
    return SandboxService(provider=provider, uow_factory=sandbox_uow_factory)


async def _workspace(service: SandboxService):
    user_id = uuid4()
    return await service.resolve(
        kind=SandboxKind.WORKSPACE,
        owner_kind=SandboxOwnerKind.USER,
        owner_id=user_id,
    )


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


async def test_ensure_provisions_once_and_then_reuses(
    service: SandboxService, provider: FakeProvider
) -> None:
    sandbox = await _workspace(service)

    first = await service.ensure(sandbox.id)
    second = await service.ensure(sandbox.id)

    assert first.provider_id == second.provider_id
    assert len(provider.created) == 1
    assert first.has(SandboxCapability.FILESYSTEM)


async def test_a_sandbox_from_a_superseded_profile_is_replaced(
    service: SandboxService, provider: FakeProvider, monkeypatch
) -> None:
    """Moving the configured digest has to reach a sandbox that already exists.

    This is the check that makes shipping a new sandbox image mean anything.
    It cannot live in the provider: `ensure` answers an existing sandbox with
    one `inspect` and returns, so a fence inside `create` never sees the case
    it is for. A stale sandbox is running an image whose in-image credential
    path and runtime headers travel with it, so adopting one yields a
    workspace that provisions and then fails every operation.
    """
    from app.modules.workspace.providers import profiles

    sandbox = await _workspace(service)
    first = await service.ensure(sandbox.id)

    monkeypatch.setattr(
        profiles.workspace_settings,
        "workspace_profile_digest",
        "sha256:" + "f" * 64,
    )
    second = await service.ensure(sandbox.id)

    assert second.provider_id != first.provider_id, "the stale one must not be reused"
    assert len(provider.created) == 2
    assert provider.created[1].profile_digest == "sha256:" + "f" * 64


async def test_an_unchanged_profile_still_reuses(
    service: SandboxService, provider: FakeProvider
) -> None:
    """The staleness check must not cost the common case its one inspect."""
    sandbox = await _workspace(service)

    first = await service.ensure(sandbox.id)
    second = await service.ensure(sandbox.id)

    assert first.provider_id == second.provider_id
    assert len(provider.created) == 1


async def test_the_recorded_profile_follows_the_configured_one(
    service: SandboxService, provider: FakeProvider, monkeypatch
) -> None:
    """Recording it only on first provision would freeze the row, and the
    staleness check would then compare that value against itself forever."""
    from app.modules.workspace.providers import profiles

    sandbox = await _workspace(service)
    await service.ensure(sandbox.id)

    for digest in ("sha256:" + "a" * 64, "sha256:" + "b" * 64):
        monkeypatch.setattr(
            profiles.workspace_settings, "workspace_profile_digest", digest
        )
        await service.ensure(sandbox.id)
        assert provider.created[-1].profile_digest == digest

    assert len(provider.created) == 3, "each move must replace, not accumulate no-ops"


async def test_concurrent_ensures_produce_one_container(
    service: SandboxService, provider: FakeProvider
) -> None:
    """A herd of tool calls arriving together must not each provision."""
    import asyncio

    sandbox = await _workspace(service)
    handles = await asyncio.gather(*(service.ensure(sandbox.id) for _ in range(5)))

    assert len({handle.provider_id for handle in handles}) == 1
    assert len(provider.created) == 1


async def test_ensuring_an_unknown_sandbox_is_definitive(
    service: SandboxService,
) -> None:
    with pytest.raises(SandboxNotFound):
        await service.ensure(uuid4())


async def test_a_rejected_create_is_recorded_and_not_retried_silently(
    service: SandboxService, provider: FakeProvider, sandbox_uow_factory
) -> None:
    sandbox = await _workspace(service)
    provider.fail_create = ProviderRejected("image is not pinned")

    with pytest.raises(SandboxRejected, match="not pinned"):
        await service.ensure(sandbox.id)

    async with sandbox_uow_factory() as uow:
        instance = await SandboxRepository(uow).current_instance(sandbox.id)
    assert instance is not None
    assert instance.last_error is not None and "pinned" in instance.last_error


# ---------------------------------------------------------------------------
# Fencing
# ---------------------------------------------------------------------------


async def test_recreating_always_moves_the_epoch(
    service: SandboxService, provider: FakeProvider
) -> None:
    """Even when the old name is free. Reusing it would let a handle held
    across the recreate address the replacement."""
    sandbox = await _workspace(service)
    first = await service.ensure(sandbox.id)

    # The container vanishes, exactly as it would if the host were restarted.
    provider.containers.clear()
    second = await service.ensure(sandbox.id)

    assert second.epoch == first.epoch + 1
    assert second.provider_id != first.provider_id
    assert naming.parse_container_name(second.provider_id) == (
        sandbox.id,
        SandboxKind.WORKSPACE,
        second.epoch,
    )


async def test_a_stopped_container_is_restarted_rather_than_replaced(
    service: SandboxService, provider: FakeProvider
) -> None:
    """Restarting keeps the epoch, because the container is the same one."""
    sandbox = await _workspace(service)
    first = await service.ensure(sandbox.id)

    provider.containers[first.provider_id] = ProviderInstance(
        provider_id=first.provider_id, name=first.provider_id, running=False
    )
    second = await service.ensure(sandbox.id)

    assert second.epoch == first.epoch
    assert len(provider.created) == 1


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


async def test_an_existing_volume_is_adopted_and_the_generation_stands_still(
    service: SandboxService, provider: FakeProvider
) -> None:
    """Adoption is the case where the user's files survived, so telling them
    the workspace was recreated would be a false alarm."""
    sandbox = await _workspace(service)
    provider.volumes[sandbox.id] = "ab-ws-legacy-token"

    handle = await service.ensure(sandbox.id)

    assert provider.created[0].volume_name == "ab-ws-legacy-token"
    assert handle.storage_generation == 1


async def test_losing_a_recorded_volume_moves_the_generation(
    service: SandboxService, provider: FakeProvider, sandbox_uow_factory
) -> None:
    """This is the one event an agent has to be told about: without it, an
    empty directory reads as "nothing was ever here"."""
    sandbox = await _workspace(service)
    provider.volumes[sandbox.id] = "ab-ws-legacy-token"
    await service.ensure(sandbox.id)

    # The disk is gone, and the row still says there was one.
    provider.volumes.clear()
    provider.containers.clear()
    handle = await service.ensure(sandbox.id)

    assert handle.storage_generation == 2
    assert provider.created[-1].volume_name == naming.volume_name(sandbox.id, 2)


async def test_a_first_ever_provision_is_not_a_recreation(
    service: SandboxService, provider: FakeProvider
) -> None:
    """A brand new workspace has lost nothing, and saying otherwise is the
    same false alarm."""
    sandbox = await _workspace(service)
    handle = await service.ensure(sandbox.id)

    assert handle.storage_generation == 1
    assert provider.created[0].volume_name == naming.volume_name(sandbox.id, 1)


async def test_a_function_sandbox_gets_no_volume(
    service: SandboxService, provider: FakeProvider
) -> None:
    """It runs an immutable artifact refetched from the gateway, so there is
    nothing durable to keep."""
    pod_id = uuid4()
    sandbox = await service.resolve(
        kind=SandboxKind.FUNCTION,
        owner_kind=SandboxOwnerKind.POD,
        owner_id=pod_id,
    )
    handle = await service.ensure(sandbox.id)

    assert provider.created[0].volume_name is None
    assert not handle.has(SandboxCapability.FILESYSTEM)
    assert handle.has(SandboxCapability.PORT_ACCESS)


# ---------------------------------------------------------------------------
# Release and destroy
# ---------------------------------------------------------------------------


async def test_release_stops_compute_and_keeps_the_disk(
    service: SandboxService, provider: FakeProvider, sandbox_uow_factory
) -> None:
    sandbox = await _workspace(service)
    handle = await service.ensure(sandbox.id)

    await service.release(sandbox.id)

    assert provider.released == [handle.provider_id]
    assert provider.destroyed_volumes == []
    async with sandbox_uow_factory() as uow:
        row = await SandboxRepository(uow).get(sandbox.id)
    assert row is not None and row.desired_state is SandboxDesiredState.RELEASED


async def test_a_released_sandbox_resumes_on_the_same_disk(
    service: SandboxService, provider: FakeProvider
) -> None:
    sandbox = await _workspace(service)
    await service.ensure(sandbox.id)
    volume = provider.created[0].volume_name
    await service.release(sandbox.id)

    resumed = await service.ensure(sandbox.id)

    assert provider.created[-1].volume_name == volume
    assert resumed.storage_generation == 1


async def test_destroy_keeps_the_disk_unless_asked(
    service: SandboxService, provider: FakeProvider
) -> None:
    """Deleting a sandbox is not the same as deleting a user's files."""
    sandbox = await _workspace(service)
    await service.ensure(sandbox.id)

    await service.destroy(sandbox.id)
    assert provider.destroyed_volumes == []

    await service.destroy(sandbox.id, delete_storage=True)
    assert provider.destroyed_volumes != []


async def test_describe_never_provisions(
    service: SandboxService, provider: FakeProvider
) -> None:
    """A status read that started a container would make every poll expensive
    and every dashboard a provisioning trigger."""
    sandbox = await _workspace(service)

    info = await service.describe(sandbox.id)

    assert info is not None
    assert info.status == "STOPPED"
    assert provider.created == []


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


async def test_a_slow_create_does_not_let_a_second_caller_provision_again(
    service: SandboxService, provider: FakeProvider
) -> None:
    """The window that matters: a row exists but its container does not yet.

    `begin_instance` records the provider object before the provider is asked
    to make it, so for the length of a create there is a row pointing at a
    container that cannot be inspected. A caller arriving in that window must
    not read "no container" as "gone, replace it" -- doing so bumps the epoch
    and builds a second sandbox, pulling the disk out from under whoever is
    already using the first.
    """
    import asyncio

    sandbox = await _workspace(service)
    creating = asyncio.Event()
    finish = asyncio.Event()
    original_create = provider.create

    async def slow_create(spec):
        creating.set()
        await finish.wait()
        return await original_create(spec)

    provider.create = slow_create  # type: ignore[method-assign]

    first = asyncio.create_task(service.ensure(sandbox.id))
    await creating.wait()

    # A second caller arrives mid-create, on a fresh singleflight entry.
    SandboxService._inflight.clear()
    second = asyncio.create_task(service.ensure(sandbox.id))
    await asyncio.sleep(0.05)

    finish.set()
    handles = await asyncio.gather(first, second)

    assert len(provider.created) == 1, provider.created
    assert len({handle.provider_id for handle in handles}) == 1
