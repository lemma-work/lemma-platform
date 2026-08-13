"""What the sweeper is willing to destroy, and what it must not touch."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from app.modules.workspace.domain.sandbox import SandboxKind, SandboxOwnerKind
from app.modules.workspace.providers.base import (
    ProcessDescriptor,
    ProviderGone,
    ProviderObject,
)
from app.modules.workspace.services.sandbox_service import SandboxService
from app.modules.workspace.services.sandbox_sweeper import SandboxSweeper
from app.modules.workspace.tests.integration.test_sandbox_service import FakeProvider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@dataclass
class SweepableProvider(FakeProvider):
    objects: list[ProviderObject] = field(default_factory=list)

    async def list_objects(self, *, deadline_at):
        return tuple(self.objects)


@pytest.fixture
def provider() -> SweepableProvider:
    return SweepableProvider()


@pytest.fixture
def service(provider: SweepableProvider, sandbox_uow_factory) -> SandboxService:
    SandboxService._inflight.clear()
    return SandboxService(provider=provider, uow_factory=sandbox_uow_factory)


@pytest.fixture
def sweeper(service: SandboxService, sandbox_uow_factory) -> SandboxSweeper:
    return SandboxSweeper(service=service, uow_factory=sandbox_uow_factory)


def _object(
    *, name: str, sandbox_id: UUID | None, epoch: int | None, legacy: bool = False
) -> ProviderObject:
    return ProviderObject(
        provider_id=name,
        name=name,
        sandbox_id=sandbox_id,
        epoch=epoch,
        running=True,
        legacy=legacy,
    )


async def _workspace(service: SandboxService):
    user_id = uuid4()
    return await service.resolve(
        kind=SandboxKind.WORKSPACE,
        owner_kind=SandboxOwnerKind.USER,
        owner_id=user_id,
    )


async def test_unidentifiable_containers_are_left_alone(
    sweeper: SandboxSweeper, provider: SweepableProvider
) -> None:
    """Leaving a stray container running is recoverable. Deleting somebody
    else's database is not."""
    provider.objects = [_object(name="postgres", sandbox_id=None, epoch=None)]

    assert await sweeper.reclaim_orphans() == ()
    assert provider.destroyed == []


async def test_a_container_with_no_row_is_reclaimed(
    sweeper: SandboxSweeper, provider: SweepableProvider
) -> None:
    """This is the one that costs money: compute the control plane forgot."""
    orphan = uuid4()
    provider.objects = [
        _object(name=f"lemma-ws-{orphan.hex}-1", sandbox_id=orphan, epoch=1)
    ]

    reclaimed = await sweeper.reclaim_orphans()

    assert reclaimed == (f"lemma-ws-{orphan.hex}-1",)
    assert provider.destroyed == [f"lemma-ws-{orphan.hex}-1"]


async def test_the_current_container_is_never_reclaimed(
    sweeper: SandboxSweeper, provider: SweepableProvider, service: SandboxService
) -> None:
    sandbox = await _workspace(service)
    handle = await service.ensure(sandbox.id)
    provider.objects = [
        _object(name=handle.provider_id, sandbox_id=sandbox.id, epoch=handle.epoch)
    ]

    assert await sweeper.reclaim_orphans() == ()
    assert provider.destroyed == []


async def test_a_superseded_epoch_is_reclaimed(
    sweeper: SandboxSweeper, provider: SweepableProvider, service: SandboxService
) -> None:
    sandbox = await _workspace(service)
    first = await service.ensure(sandbox.id)
    provider.containers.clear()
    second = await service.ensure(sandbox.id)
    assert second.epoch > first.epoch

    provider.objects = [
        _object(name=first.provider_id, sandbox_id=sandbox.id, epoch=first.epoch),
        _object(name=second.provider_id, sandbox_id=sandbox.id, epoch=second.epoch),
    ]

    reclaimed = await sweeper.reclaim_orphans()

    assert reclaimed == (first.provider_id,)


async def test_a_legacy_container_survives_until_a_replacement_exists(
    sweeper: SandboxSweeper, provider: SweepableProvider, service: SandboxService
) -> None:
    """While both provisioning paths exist, the pre-cutover container may be
    the only one serving this sandbox. Reclaiming it would kill a live
    workspace mid-session."""
    sandbox = await _workspace(service)
    provider.objects = [
        _object(
            name="ab-w-abc123-def456",
            sandbox_id=sandbox.id,
            epoch=None,
            legacy=True,
        )
    ]

    assert await sweeper.reclaim_orphans() == ()
    assert provider.destroyed == []


async def test_a_legacy_container_is_reclaimed_once_superseded(
    sweeper: SandboxSweeper, provider: SweepableProvider, service: SandboxService
) -> None:
    sandbox = await _workspace(service)
    handle = await service.ensure(sandbox.id)
    provider.objects = [
        _object(
            name="ab-w-abc123-def456",
            sandbox_id=sandbox.id,
            epoch=None,
            legacy=True,
        ),
        _object(name=handle.provider_id, sandbox_id=sandbox.id, epoch=handle.epoch),
    ]

    reclaimed = await sweeper.reclaim_orphans()

    assert reclaimed == ("ab-w-abc123-def456",)


async def test_a_dry_run_reports_without_destroying(
    sweeper: SandboxSweeper, provider: SweepableProvider
) -> None:
    orphan = uuid4()
    provider.objects = [
        _object(name=f"lemma-ws-{orphan.hex}-1", sandbox_id=orphan, epoch=1)
    ]

    reclaimed = await sweeper.reclaim_orphans(dry_run=True)

    assert reclaimed == (f"lemma-ws-{orphan.hex}-1",)
    assert provider.destroyed == []


async def test_one_failure_does_not_stop_the_sweep(
    sweeper: SandboxSweeper, provider: SweepableProvider
) -> None:
    first, second = uuid4(), uuid4()
    provider.objects = [
        _object(name=f"lemma-ws-{first.hex}-1", sandbox_id=first, epoch=1),
        _object(name=f"lemma-ws-{second.hex}-1", sandbox_id=second, epoch=1),
    ]
    original = provider.destroy
    calls: list[str] = []

    async def flaky(name: str, *, deadline_at):
        calls.append(name)
        if len(calls) == 1:
            raise RuntimeError("engine hiccup")
        await original(name, deadline_at=deadline_at)

    provider.destroy = flaky  # type: ignore[method-assign]

    await sweeper.reclaim_orphans()

    assert len(calls) == 2, "a failure on one object must not abandon the rest"


async def test_idle_release_stops_quiet_sandboxes_and_keeps_their_disks(
    sweeper: SandboxSweeper, provider: SweepableProvider, service: SandboxService
) -> None:
    sandbox = await _workspace(service)
    handle = await service.ensure(sandbox.id)

    released = await sweeper.release_idle(idle_after_seconds=0)

    assert released >= 1
    assert handle.provider_id in provider.released
    assert provider.destroyed_volumes == []


async def test_recently_used_sandboxes_are_not_released(
    sweeper: SandboxSweeper, service: SandboxService
) -> None:
    sandbox = await _workspace(service)
    await service.ensure(sandbox.id)

    assert await sweeper.release_idle(idle_after_seconds=3600) == 0


async def test_a_sandbox_with_running_work_is_not_released(
    sweeper: SandboxSweeper, provider: SweepableProvider, service: SandboxService
) -> None:
    """Idle is measured from the last ensure, not the last thing the sandbox did.

    A single long tool call or function invocation therefore looks idle for its
    whole duration, and without a liveness check the sweep would stop compute
    underneath work that is still running.
    """
    sandbox = await _workspace(service)
    handle = await service.ensure(sandbox.id)
    provider.processes = [
        ProcessDescriptor(process_id="op-1", state="running", exit_code=None)
    ]

    assert await sweeper.release_idle(idle_after_seconds=0) == 0
    assert handle.provider_id not in provider.released


async def test_a_sandbox_whose_work_has_finished_is_released(
    sweeper: SandboxSweeper, provider: SweepableProvider, service: SandboxService
) -> None:
    sandbox = await _workspace(service)
    handle = await service.ensure(sandbox.id)
    provider.processes = [
        ProcessDescriptor(process_id="op-1", state="exited", exit_code=0)
    ]

    assert await sweeper.release_idle(idle_after_seconds=0) >= 1
    assert handle.provider_id in provider.released


async def test_a_sandbox_that_cannot_be_probed_is_left_alone(
    sweeper: SandboxSweeper, provider: SweepableProvider, service: SandboxService
) -> None:
    """A failed probe is not evidence of idleness."""

    sandbox = await _workspace(service)
    await service.ensure(sandbox.id)

    async def _unreachable(instance, *, deadline_at):
        raise ProviderGone("sandbox is unreachable")

    provider.list_processes = _unreachable  # type: ignore[method-assign]

    assert await sweeper.release_idle(idle_after_seconds=0) == 0


# --- volumes -----------------------------------------------------------------
#
# A workspace disk deliberately outlives every container that mounts it, so the
# container delete keeps it. Nothing ever collected it afterwards: `destroy_volume`
# existed on every provider and was called by nothing, and `list_objects` returned
# containers only. Every sandbox ever created leaked its volume, forever.


def _volume(*, name: str, sandbox_id: UUID | None, storage_generation: int | None):
    return ProviderObject(
        provider_id=name,
        name=name,
        sandbox_id=sandbox_id,
        epoch=None,
        running=False,
        kind="volume",
        storage_generation=storage_generation,
    )


async def test_a_volume_with_no_row_is_reclaimed(
    sweeper: SandboxSweeper, provider: SweepableProvider
) -> None:
    """The disk of a sandbox that no longer exists. This is the 50GB."""
    orphan = uuid4()
    name = f"lemma-vol-{orphan.hex}-1"
    provider.objects = [_volume(name=name, sandbox_id=orphan, storage_generation=1)]

    reclaimed = await sweeper.reclaim_orphans()

    assert reclaimed == (name,)
    assert provider.destroyed_volumes == [name]
    assert provider.destroyed == []


async def test_a_live_sandboxs_volume_survives_an_epoch_bump(
    sweeper: SandboxSweeper, provider: SweepableProvider, service: SandboxService
) -> None:
    """The regression this rule exists for.

    A volume is the disk every container generation mounts, so judging it by
    epoch the way a container is judged would delete a live workspace the first
    time its container restarted.
    """
    sandbox = await _workspace(service)
    first = await service.ensure(sandbox.id)
    provider.containers.clear()
    second = await service.ensure(sandbox.id)
    assert second.epoch > first.epoch

    name = f"lemma-vol-{sandbox.id.hex}-1"
    provider.objects = [
        _volume(name=name, sandbox_id=sandbox.id, storage_generation=1),
        _object(name=second.provider_id, sandbox_id=sandbox.id, epoch=second.epoch),
    ]

    assert await sweeper.reclaim_orphans() == ()
    assert provider.destroyed_volumes == []


async def test_a_superseded_storage_generation_is_reclaimed(
    sweeper: SandboxSweeper,
    provider: SweepableProvider,
    service: SandboxService,
    sandbox_uow_factory,
) -> None:
    """A new disk generation makes the old disk garbage -- the one thing that
    genuinely supersedes a volume."""
    from app.modules.workspace.infrastructure.sandbox_repository import (
        SandboxRepository,
    )

    sandbox = await _workspace(service)
    async with sandbox_uow_factory() as uow:
        await SandboxRepository(uow).bump_storage_generation(sandbox.id)
        await uow.commit()

    stale = f"lemma-vol-{sandbox.id.hex}-1"
    current = f"lemma-vol-{sandbox.id.hex}-2"
    provider.objects = [
        _volume(name=stale, sandbox_id=sandbox.id, storage_generation=1),
        _volume(name=current, sandbox_id=sandbox.id, storage_generation=2),
    ]

    reclaimed = await sweeper.reclaim_orphans()

    assert reclaimed == (stale,)
    assert provider.destroyed_volumes == [stale]


async def test_a_volume_of_unknown_generation_is_left_alone_while_its_sandbox_lives(
    sweeper: SandboxSweeper, provider: SweepableProvider, service: SandboxService
) -> None:
    """Pre-cutover volumes embed a random token, so their generation cannot be
    read. Unparseable means unknown, never "generation zero"."""
    sandbox = await _workspace(service)
    provider.objects = [
        _volume(name="lemma-vol-legacytoken", sandbox_id=sandbox.id, storage_generation=None)
    ]

    assert await sweeper.reclaim_orphans() == ()
    assert provider.destroyed_volumes == []


async def test_an_unidentifiable_volume_is_left_alone(
    sweeper: SandboxSweeper, provider: SweepableProvider
) -> None:
    provider.objects = [
        _volume(name="someone-elses-data", sandbox_id=None, storage_generation=None)
    ]

    assert await sweeper.reclaim_orphans() == ()
    assert provider.destroyed_volumes == []


async def test_containers_are_reclaimed_before_the_volumes_they_mount(
    sweeper: SandboxSweeper, provider: SweepableProvider
) -> None:
    """Docker refuses to delete a volume a container still mounts, so taking the
    container first is what makes the disk collectable in the same sweep."""
    orphan = uuid4()
    order: list[str] = []
    provider.objects = [
        _volume(name=f"lemma-vol-{orphan.hex}-1", sandbox_id=orphan, storage_generation=1),
        _object(name=f"lemma-ws-{orphan.hex}-1", sandbox_id=orphan, epoch=1),
    ]

    async def record_container(name, *, deadline_at):
        order.append(f"container:{name}")

    async def record_volume(name, *, deadline_at):
        order.append(f"volume:{name}")

    provider.destroy = record_container
    provider.destroy_volume = record_volume

    await sweeper.reclaim_orphans()

    assert order == [
        f"container:lemma-ws-{orphan.hex}-1",
        f"volume:lemma-vol-{orphan.hex}-1",
    ]
