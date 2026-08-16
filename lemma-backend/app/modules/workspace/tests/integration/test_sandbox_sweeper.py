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
    ProviderStorageKind,
)
from app.modules.workspace.infrastructure.sandbox_repository import (
    SandboxRepository,
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


class _OpaqueIdProvider(SweepableProvider):
    """A provider whose ids are nothing like its names, which E2B's are not.

    `FakeProvider.create` returns `provider_id=spec.name`, so every other test
    here has the two coincide and cannot see a caller that confuses them. E2B
    mints its own (`i8fdef5eyd8zxnysl6bor`) against a name of
    `lemma-ws-<hex>-<epoch>`, and the process index is written under the id.
    """

    async def create(self, spec):
        instance = await super().create(spec)
        opaque = ProviderInstance(
            provider_id=f"i{abs(hash(spec.name)):x}",
            name=spec.name,
            volume_name=instance.volume_name,
            running=True,
            storage_adopted=instance.storage_adopted,
            profile_digest=instance.profile_digest,
        )
        self.containers[instance.name] = instance
        self.containers[opaque.provider_id] = opaque
        self.probed_with = getattr(self, "probed_with", [])
        return opaque

    async def list_processes(self, instance, *, deadline_at):
        self.probed_with = getattr(self, "probed_with", [])
        self.probed_with.append(instance.provider_id)
        return await super().list_processes(instance, deadline_at=deadline_at)


async def test_the_liveness_probe_addresses_the_sandbox_by_its_provider_id(
    sandbox_uow_factory,
) -> None:
    """The idle check has to reach the sandbox it is asking about.

    E2B keys its process index by the real sandbox id, so a probe built from
    the container name reads an index that is always empty -- an idle check
    that returns is not an idle check that happened, and the sweep would pause
    a sandbox mid-work. That is the exact harm the check exists to prevent, and
    it is invisible wherever id and name coincide.
    """
    SandboxService._inflight.clear()
    SandboxService._recent.clear()
    provider = _OpaqueIdProvider()
    service = SandboxService(provider=provider, uow_factory=sandbox_uow_factory)
    sweeper = SandboxSweeper(service=service, uow_factory=sandbox_uow_factory)
    sandbox = await _workspace(service)
    handle = await service.ensure(sandbox.id)
    provider.processes = [
        ProcessDescriptor(process_id="op-1", state="running", exit_code=None)
    ]

    released = await sweeper.release_idle(idle_after_seconds=0)

    assert provider.probed_with == [handle.provider_id], (
        "the probe must carry the provider id, not the container name"
    )
    assert released == 0, "a sandbox with live work must not be paused"


async def test_a_killed_process_does_not_pin_its_sandbox_forever(
    sweeper: SandboxSweeper, provider: SweepableProvider, service: SandboxService
) -> None:
    """Busy-ness is a state, not an absent exit code.

    E2B records a cancelled process with `exit_code=None`
    (`e2b_output.record_cancelled`), so reading busy-ness off the exit code made
    every process an agent killed pin its sandbox as busy for the hour the
    output buffer retains it, and the idle sweep never released it. Agents kill
    processes exactly when a tool call looks stuck -- so the sandbox that had
    just frustrated someone was then the one that could never be reclaimed.
    """
    sandbox = await _workspace(service)
    handle = await service.ensure(sandbox.id)
    provider.processes = [
        ProcessDescriptor(process_id="op-1", state="cancelled", exit_code=None)
    ]

    released = await sweeper.release_idle(idle_after_seconds=0)

    assert released == 1, "a sandbox whose only process was killed is idle"
    assert handle.provider_id in provider.released


# ---------------------------------------------------------------------------
# Whose epoch decides anything
# ---------------------------------------------------------------------------


@dataclass
class _SandboxIsTheDiskProvider(SweepableProvider):
    """A provider where destroying the object destroys the user's files.

    E2B works this way, and `ProviderStorageKind.SANDBOX_NATIVE` says what
    follows from it: "one object is both … the fence is the provider's own id
    rather than an epoch in a name".
    """

    storage_kind: ProviderStorageKind = ProviderStorageKind.SANDBOX_NATIVE


async def test_an_old_epoch_never_reclaims_a_sandbox_that_is_also_the_disk(
    sandbox_uow_factory,
) -> None:
    """The regression, and it was destroying live user workspaces.

    The row's epoch advances on every provision, including the ones that adopt
    an existing sandbox. The epoch it was compared against is read from provider
    metadata that nothing can update -- the re-stamp was guarded on
    `set_metadata`, which the E2B SDK does not have -- so it stayed frozen at
    the value the first create wrote. "epoch 1 is behind 6" was therefore
    permanently true for every workspace a user had kept, and the sweep called
    destroy on it every five minutes. It only ever failed to delete them because
    destroy could not address a paused sandbox, which has since been fixed.
    """
    provider = _SandboxIsTheDiskProvider()
    service = SandboxService(provider=provider, uow_factory=sandbox_uow_factory)
    sweeper = SandboxSweeper(service=service, uow_factory=sandbox_uow_factory)
    sandbox = await _workspace(service)
    await service.ensure(sandbox.id)

    name = f"lemma-ws-{sandbox.id.hex}-1"
    provider.objects = [_object(name=name, sandbox_id=sandbox.id, epoch=1)]
    async with sandbox_uow_factory() as uow:
        await SandboxRepository(uow).bump_epoch(sandbox.id)
        await SandboxRepository(uow).bump_epoch(sandbox.id)

    reclaimed = await sweeper.reclaim_orphans()

    assert provider.destroyed == [], (
        "the sweep destroyed a live workspace, and here the sandbox is the disk"
    )
    assert reclaimed == ()


async def test_an_old_epoch_still_reclaims_where_the_disk_is_separate(
    sweeper: SandboxSweeper, provider: SweepableProvider, service: SandboxService
) -> None:
    """The exemption must stay narrow.

    On a VOLUME provider the container and the volume are different objects, so
    a superseded container is genuinely garbage and reclaiming it costs nothing
    but a cold start. Turning the epoch off everywhere would leak those forever.
    """
    sandbox = await _workspace(service)
    await service.ensure(sandbox.id)

    name = f"lemma-ws-{sandbox.id.hex}-1"
    provider.objects = [_object(name=name, sandbox_id=sandbox.id, epoch=1)]
    async with sweeper._uow_factory() as uow:
        await SandboxRepository(uow).bump_epoch(sandbox.id)

    reclaimed = await sweeper.reclaim_orphans()

    assert reclaimed == (name,)
    assert provider.destroyed == [name]
