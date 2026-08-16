"""What the sweeper is willing to destroy, and what it must not touch."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from app.modules.workspace.domain.sandbox import SandboxKind, SandboxOwnerKind
from app.modules.workspace.providers.base import (
    ProcessDescriptor,
    ProviderGone,
    ProviderInstance,
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
    # Both caches are class attributes, so they outlive the instance and leak
    # between tests. Only `_inflight` was being cleared, which left `_recent`
    # to answer the second `ensure` of a test that had just emptied the
    # provider -- so whether a test saw a re-provision depended on which tests
    # ran before it.
    SandboxService._inflight.clear()
    SandboxService._recent.clear()
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
    # Losing the container is exactly what `forget` is for: `ensure` reuses a
    # just-provisioned handle for a few seconds, so without this the second
    # call answers from memory and no new epoch is ever minted.
    provider.containers.clear()
    service.forget(sandbox.id)
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


async def test_an_object_the_provider_cannot_reap_is_not_reported_as_reclaimed(
    sweeper: SandboxSweeper, provider: SweepableProvider
) -> None:
    """A destroy that returns is not a destroy that happened.

    On E2B a paused sandbox lists like any other but survives being killed, so
    this loop logged eighteen reclaims every five minutes for hours -- the same
    eighteen ids -- while the account held ninety-nine paused sandboxes and the
    count never moved. Silence about that is worse than the leak: the sweep
    looks like it is working.
    """
    orphan = uuid4()
    name = f"lemma-ws-{orphan.hex}-1"
    provider.objects = [_object(name=name, sandbox_id=orphan, epoch=1)]
    # Accepts the call, keeps the object -- what killing a paused sandbox does.
    provider.containers[name] = ProviderInstance(
        provider_id=name, name=name, running=False
    )

    async def _destroy_that_does_nothing(destroyed_name: str, *, deadline_at) -> None:
        del deadline_at
        provider.destroyed.append(destroyed_name)

    provider.destroy = _destroy_that_does_nothing  # type: ignore[method-assign]

    reclaimed = await sweeper.reclaim_orphans()

    assert provider.destroyed == [name], "the destroy must still be attempted"
    assert reclaimed == (), "nothing was reclaimed, so nothing may be claimed"


async def test_a_destroy_that_works_is_still_reported(
    sweeper: SandboxSweeper, provider: SweepableProvider
) -> None:
    """The confirmation must not turn every real reclaim into a warning."""
    orphan = uuid4()
    name = f"lemma-ws-{orphan.hex}-1"
    provider.objects = [_object(name=name, sandbox_id=orphan, epoch=1)]
    provider.containers[name] = ProviderInstance(
        provider_id=name, name=name, running=True
    )

    reclaimed = await sweeper.reclaim_orphans()

    assert reclaimed == (name,)
    assert provider.destroyed == [name]
