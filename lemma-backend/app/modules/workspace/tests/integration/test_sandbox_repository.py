from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from app.modules.workspace.domain.sandbox import (
    SandboxCapability,
    SandboxDesiredState,
    SandboxInstanceState,
    SandboxKind,
    SandboxOwnerKind,
)
from app.modules.workspace.infrastructure.sandbox_repository import (
    SandboxRepository,
    utcnow,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _workspace(repository: SandboxRepository, *, slug: str = "default"):
    user_id = uuid4()
    return await repository.ensure_row(
        sandbox_id=user_id,
        kind=SandboxKind.WORKSPACE,
        owner_kind=SandboxOwnerKind.USER,
        owner_id=user_id,
        slug=slug,
    )


async def test_ensure_row_is_idempotent_per_owner_and_slug(
    sandbox_repository: SandboxRepository,
) -> None:
    user_id = uuid4()
    first = await sandbox_repository.ensure_row(
        sandbox_id=user_id,
        kind=SandboxKind.WORKSPACE,
        owner_kind=SandboxOwnerKind.USER,
        owner_id=user_id,
        slug="default",
    )
    # A different id must not create a second default: the slug is the identity.
    second = await sandbox_repository.ensure_row(
        sandbox_id=uuid4(),
        kind=SandboxKind.WORKSPACE,
        owner_kind=SandboxOwnerKind.USER,
        owner_id=user_id,
        slug="default",
    )

    assert first.id == second.id == user_id
    assert (
        len(
            await sandbox_repository.list_for_owner(
                kind=SandboxKind.WORKSPACE,
                owner_kind=SandboxOwnerKind.USER,
                owner_id=user_id,
            )
        )
        == 1
    )


async def test_a_user_can_hold_several_named_workspaces(
    sandbox_repository: SandboxRepository,
) -> None:
    """The whole point of the table: identity is no longer the user id."""
    user_id = uuid4()
    for slug in ("default", "scratch", "research"):
        await sandbox_repository.ensure_row(
            sandbox_id=uuid4() if slug != "default" else user_id,
            kind=SandboxKind.WORKSPACE,
            owner_kind=SandboxOwnerKind.USER,
            owner_id=user_id,
            slug=slug,
        )

    owned = await sandbox_repository.list_for_owner(
        kind=SandboxKind.WORKSPACE,
        owner_kind=SandboxOwnerKind.USER,
        owner_id=user_id,
    )
    assert [sandbox.slug for sandbox in owned] == ["default", "research", "scratch"]


async def test_a_pod_function_runtime_shares_the_table_without_colliding(
    sandbox_repository: SandboxRepository,
) -> None:
    """Same id space, different kind: a pod id and a user id may coincide."""
    shared_id = uuid4()
    workspace = await sandbox_repository.ensure_row(
        sandbox_id=shared_id,
        kind=SandboxKind.WORKSPACE,
        owner_kind=SandboxOwnerKind.USER,
        owner_id=shared_id,
        slug="default",
    )
    function = await sandbox_repository.ensure_row(
        sandbox_id=uuid4(),
        kind=SandboxKind.FUNCTION,
        owner_kind=SandboxOwnerKind.POD,
        owner_id=shared_id,
        slug="default",
    )

    assert workspace.id != function.id
    assert function.kind is SandboxKind.FUNCTION
    # Sharing the table must not blur the capability sets.
    assert workspace.has(SandboxCapability.DURABLE_STORAGE)
    assert not function.has(SandboxCapability.DURABLE_STORAGE)
    assert not function.has(SandboxCapability.FILESYSTEM)


async def test_bumping_the_epoch_changes_the_fence(
    sandbox_repository: SandboxRepository,
) -> None:
    sandbox = await _workspace(sandbox_repository)
    assert sandbox.epoch == 1

    assert await sandbox_repository.bump_epoch(sandbox.id) == 2
    assert await sandbox_repository.bump_epoch(sandbox.id) == 3

    reloaded = await sandbox_repository.get(sandbox.id)
    assert reloaded is not None
    assert reloaded.epoch == 3
    # Replacing compute must not imply the disk was replaced.
    assert reloaded.storage_generation == 1


async def test_storage_generation_moves_only_when_the_disk_is_replaced(
    sandbox_repository: SandboxRepository,
) -> None:
    sandbox = await _workspace(sandbox_repository)

    await sandbox_repository.bump_epoch(sandbox.id)
    reloaded = await sandbox_repository.get(sandbox.id)
    assert reloaded is not None and reloaded.storage_generation == 1

    assert await sandbox_repository.bump_storage_generation(sandbox.id) == 2
    reloaded = await sandbox_repository.get(sandbox.id)
    assert reloaded is not None and reloaded.storage_generation == 2


async def test_volume_id_starts_null_so_it_can_be_adopted_not_derived(
    sandbox_repository: SandboxRepository,
) -> None:
    """The legacy volume name embeds a random token that exists nowhere here.

    Starting NULL is what lets the first ensure find the pre-consolidation
    volume by label instead of inventing a name and stranding the user's files.
    """
    sandbox = await _workspace(sandbox_repository)
    assert sandbox.provider_volume_id is None

    await sandbox_repository.set_provider_volume(sandbox.id, "ab-ws-deadbeef")
    reloaded = await sandbox_repository.get(sandbox.id)
    assert reloaded is not None
    assert reloaded.provider_volume_id == "ab-ws-deadbeef"


async def test_an_instance_is_recorded_before_the_provider_is_called(
    sandbox_repository: SandboxRepository,
) -> None:
    """A row with no container is recoverable; a container with no row is not."""
    sandbox = await _workspace(sandbox_repository)
    instance = await sandbox_repository.begin_instance(
        sandbox_id=sandbox.id,
        provider="docker",
        provider_id=f"lemma-workspace-{sandbox.id.hex}-1",
        provider_volume_id="ab-ws-deadbeef",
        epoch=sandbox.epoch,
    )

    assert instance.state is SandboxInstanceState.CREATING
    assert instance.ready_at is None

    current = await sandbox_repository.current_instance(sandbox.id)
    assert current is not None and current.id == instance.id

    await sandbox_repository.mark_instance_ready(instance.id)
    current = await sandbox_repository.current_instance(sandbox.id)
    assert current is not None
    assert current.state is SandboxInstanceState.READY
    assert current.ready_at is not None


async def test_current_instance_follows_the_epoch(
    sandbox_repository: SandboxRepository,
) -> None:
    """After a recreate, the old instance is no longer current even though it
    still exists as a row the sweeper has to reclaim."""
    sandbox = await _workspace(sandbox_repository)
    old = await sandbox_repository.begin_instance(
        sandbox_id=sandbox.id,
        provider="docker",
        provider_id=f"lemma-workspace-{sandbox.id.hex}-1",
        provider_volume_id=None,
        epoch=1,
    )
    await sandbox_repository.mark_instance_ready(old.id)

    epoch = await sandbox_repository.bump_epoch(sandbox.id)
    assert await sandbox_repository.current_instance(sandbox.id) is None

    new = await sandbox_repository.begin_instance(
        sandbox_id=sandbox.id,
        provider="docker",
        provider_id=f"lemma-workspace-{sandbox.id.hex}-{epoch}",
        provider_volume_id=None,
        epoch=epoch,
    )
    current = await sandbox_repository.current_instance(sandbox.id)
    assert current is not None and current.id == new.id

    # The superseded instance is still reclaimable compute.
    reclaimable = await sandbox_repository.list_reclaimable_instances(provider="docker")
    assert {i.id for i in reclaimable} >= {old.id, new.id}


async def test_destroyed_instances_are_not_reclaimable(
    sandbox_repository: SandboxRepository,
) -> None:
    sandbox = await _workspace(sandbox_repository)
    instance = await sandbox_repository.begin_instance(
        sandbox_id=sandbox.id,
        provider="docker",
        provider_id="lemma-workspace-x-1",
        provider_volume_id=None,
        epoch=1,
    )
    await sandbox_repository.mark_instance_destroyed(instance.id)

    reclaimable = await sandbox_repository.list_reclaimable_instances(provider="docker")
    assert instance.id not in {i.id for i in reclaimable}


async def test_errored_instances_stay_reclaimable(
    sandbox_repository: SandboxRepository,
) -> None:
    """A failed create may still have left compute behind, so it is swept."""
    sandbox = await _workspace(sandbox_repository)
    instance = await sandbox_repository.begin_instance(
        sandbox_id=sandbox.id,
        provider="docker",
        provider_id="lemma-workspace-y-1",
        provider_volume_id=None,
        epoch=1,
    )
    await sandbox_repository.mark_instance_error(instance.id, "provider timeout")

    reclaimable = await sandbox_repository.list_reclaimable_instances(provider="docker")
    assert instance.id in {i.id for i in reclaimable}


async def test_touching_a_sandbox_takes_it_out_of_the_idle_sweep(
    sandbox_repository: SandboxRepository,
) -> None:
    """Activity is what idle reclamation measures, so a touch must move it."""
    idle = await _workspace(sandbox_repository, slug="idle")
    active = await _workspace(sandbox_repository, slug="active")

    # Everything is idle as of now...
    cutoff = utcnow()
    assert {s.id for s in await sandbox_repository.list_idle(idle_before=cutoff)} >= {
        idle.id,
        active.id,
    }

    # ...until one of them is used.
    await sandbox_repository.touch(active.id)
    stale = {s.id for s in await sandbox_repository.list_idle(idle_before=cutoff)}
    assert idle.id in stale
    assert active.id not in stale


async def test_released_sandboxes_are_not_idle_work(
    sandbox_repository: SandboxRepository,
) -> None:
    """Nothing is running, so there is nothing for the idle sweep to reclaim."""
    released = await _workspace(sandbox_repository, slug="released")
    await sandbox_repository.set_desired_state(
        released.id, SandboxDesiredState.RELEASED
    )

    stale = {
        s.id
        for s in await sandbox_repository.list_idle(
            idle_before=utcnow() + timedelta(seconds=1)
        )
    }
    assert released.id not in stale


async def test_deleted_sandboxes_disappear_from_owner_listings(
    sandbox_repository: SandboxRepository,
) -> None:
    user_id = uuid4()
    keep = await sandbox_repository.ensure_row(
        sandbox_id=user_id,
        kind=SandboxKind.WORKSPACE,
        owner_kind=SandboxOwnerKind.USER,
        owner_id=user_id,
        slug="default",
    )
    gone = await sandbox_repository.ensure_row(
        sandbox_id=uuid4(),
        kind=SandboxKind.WORKSPACE,
        owner_kind=SandboxOwnerKind.USER,
        owner_id=user_id,
        slug="scratch",
    )
    await sandbox_repository.set_desired_state(gone.id, SandboxDesiredState.DELETED)

    owned = await sandbox_repository.list_for_owner(
        kind=SandboxKind.WORKSPACE,
        owner_kind=SandboxOwnerKind.USER,
        owner_id=user_id,
    )
    assert [sandbox.id for sandbox in owned] == [keep.id]
