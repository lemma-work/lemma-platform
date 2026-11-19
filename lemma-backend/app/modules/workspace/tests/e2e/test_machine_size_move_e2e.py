"""A size move, as the rows say it: who is kept off the machine, and when a
move given up is not tried again.

Nothing here starts a machine: the resolver and the marking decide from rows.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import update

from app.core.api.dependencies import get_uow_factory
from app.modules.workspace.domain.machine import Machine, MachineSize
from app.modules.workspace.domain.machine_access import (
    CallerKind,
    MachineAccess,
    MachineCaller,
)
from app.modules.workspace.domain.machine_errors import MachineMoving
from app.modules.workspace.infrastructure.models import SandboxModel
from app.modules.workspace.services.machine_resolver import MachineResolver
from app.modules.workspace.services.size_migration import MoveState, SizeMigration

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def _resolve(owner: UUID, need: MachineAccess) -> Machine:
    async with get_uow_factory()() as uow:
        resolved = await MachineResolver(uow).resolve(
            "me", MachineCaller(CallerKind.USER, user_id=owner), need=need
        )
        await uow.commit()
    return resolved.machine


async def _set_move(machine_id: UUID, **values: object) -> None:
    async with get_uow_factory()() as uow:
        await uow.session.execute(
            update(SandboxModel).where(SandboxModel.id == machine_id).values(**values)
        )
        await uow.commit()


@pytest.fixture
async def owner(scenario) -> UUID:
    return UUID((await scenario.create_user("size-move"))["id"])


@pytest.mark.parametrize("state", list(MoveState))
async def test_a_machine_being_moved_is_refused_for_use_but_still_seen(
    owner, state
) -> None:
    machine = await _resolve(owner, MachineAccess.USE)
    await _set_move(
        machine.id, size_migration_state=state.value, size_migration_target="2x4"
    )

    with pytest.raises(MachineMoving):
        await _resolve(owner, MachineAccess.USE)
    with pytest.raises(MachineMoving):
        await _resolve(owner, MachineAccess.MANAGE)
    seen = await _resolve(owner, MachineAccess.STATUS)

    assert seen.moving is True


async def test_a_move_given_up_is_not_marked_again_for_the_same_size(owner) -> None:
    machine = await _resolve(owner, MachineAccess.USE)
    migration = SizeMigration(get_uow_factory())
    # What `_abandon` leaves: no state, the target it failed to reach.
    await _set_move(
        machine.id,
        size_migration_state=None,
        size_migration_target="2x4",
        size_migration_attempts=5,
    )

    assert await migration.begin(machine, to=MachineSize.parse("2x4")) is False
    assert (await _resolve(owner, MachineAccess.USE)).moving is False
    assert await migration.begin(machine, to=MachineSize.parse("4x8")) is True
