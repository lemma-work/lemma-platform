"""A size move resumes from the state on the machine row, never from the start.

The row is an object the test holds; the steps are recorded rather than run,
so what is tested is which steps a given state leads to.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.workspace.domain.machine_errors import MachineConflict
from app.modules.workspace.services import size_migration
from app.modules.workspace.services.size_migration import (
    MAX_ATTEMPTS,
    MoveState,
    SizeMigration,
)


class _Move(SizeMigration):
    def __init__(self, row: SimpleNamespace, *, fail_at: str | None = None) -> None:
        super().__init__(None)  # type: ignore[arg-type]
        self.row = row
        self.steps: list[str] = []
        self.fail_at = fail_at

    async def _row(self, machine_id):
        return self.row

    async def _set(self, machine_id, **values):
        for key, value in values.items():
            setattr(self.row, key, value)

    def _step(self, name: str) -> None:
        self.steps.append(name)
        if name == self.fail_at:
            raise MachineConflict("it broke", code="TEST")

    async def _pack_step(self, machine_id, acting_user):
        self._step("pack")
        await self._set(
            machine_id,
            size_migration_state=MoveState.PACKED.value,
            size_migration_carry_key="carry/key.tgz",
        )
        return True, "carry/key.tgz"

    async def _destroy_step(self, machine_id, target, *, carried):
        self._step(f"destroy carried={carried}")
        await self._set(machine_id, size_migration_state=MoveState.RESIZED.value)

    async def _finish_step(self, machine_id, acting_user, carry_key):
        self._step(f"finish {carry_key}")
        await self._set(machine_id, size_migration_state=None)


def _row(state: MoveState, carry_key: str | None = None, attempts: int = 0):
    return SimpleNamespace(
        size_migration_state=state.value,
        size_migration_target="2x4",
        size_migration_carry_key=carry_key,
        size_migration_attempts=attempts,
        created_by=None,
        owner_id=uuid4(),
    )


async def test_a_fresh_move_runs_every_step() -> None:
    move = _Move(_row(MoveState.STARTED))

    assert await move.advance(uuid4(), acting_user=uuid4())
    assert move.steps == ["pack", "destroy carried=True", "finish carry/key.tgz"]


async def test_a_move_that_was_packed_does_not_pack_again() -> None:
    move = _Move(_row(MoveState.PACKED, carry_key="carry/old.tgz"))

    await move.advance(uuid4(), acting_user=uuid4())

    assert move.steps == ["destroy carried=True", "finish carry/old.tgz"]


async def test_a_move_past_the_destroy_only_finishes() -> None:
    move = _Move(_row(MoveState.RESIZED, carry_key="carry/old.tgz"))

    await move.advance(uuid4(), acting_user=uuid4())

    assert move.steps == ["finish carry/old.tgz"]


async def test_a_failure_keeps_the_state_and_the_carry_key() -> None:
    row = _row(MoveState.STARTED)
    move = _Move(row, fail_at="finish carry/key.tgz")

    with pytest.raises(MachineConflict):
        await move.advance(uuid4(), acting_user=uuid4())

    assert row.size_migration_state == MoveState.RESIZED.value
    assert row.size_migration_carry_key == "carry/key.tgz"
    assert row.size_migration_attempts == 1


async def test_a_move_that_never_got_past_started_is_given_up_and_nothing_lost(
    monkeypatch,
) -> None:
    row = _row(MoveState.STARTED, attempts=MAX_ATTEMPTS)
    move = _Move(row)

    await size_migration._note_failure(move, uuid4(), MachineConflict("x", code="T"))

    assert row.size_migration_state is None


async def test_a_stuck_move_past_the_destroy_keeps_its_carry_key() -> None:
    row = _row(MoveState.RESIZED, carry_key="carry/old.tgz", attempts=MAX_ATTEMPTS)
    move = _Move(row)

    await size_migration._note_failure(move, uuid4(), MachineConflict("x", code="T"))

    assert row.size_migration_state == MoveState.RESIZED.value
    assert row.size_migration_carry_key == "carry/old.tgz"
