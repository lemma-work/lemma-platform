"""A machine the metering pass saw resume is looked at again before its
services are brought back: the idle release runs in between, and restarting
a service on a machine released that tick woke it straight back up."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.workspace.domain.sandbox import (
    SandboxDesiredState,
    SandboxKind,
    SandboxOwnerKind,
)
from app.modules.workspace.infrastructure.sandbox_repository import SandboxRepository
from app.modules.workspace.services.machine_housekeeping import _still_running

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _running_machine(sandbox_uow_factory):
    owner = uuid4()
    async with sandbox_uow_factory() as uow:
        repository = SandboxRepository(uow)
        sandbox = await repository.ensure_row(
            sandbox_id=owner,
            kind=SandboxKind.WORKSPACE,
            owner_kind=SandboxOwnerKind.USER,
            owner_id=owner,
            slug="default",
        )
        instance = await repository.begin_instance(
            sandbox_id=sandbox.id,
            provider="fake",
            provider_id=f"lemma-workspace-{sandbox.id.hex}-1",
            provider_volume_id=None,
            epoch=sandbox.epoch,
        )
        await repository.mark_instance_ready(instance.id)
        await uow.commit()
    return sandbox, instance


async def test_a_machine_still_up_is_brought_back(sandbox_uow_factory) -> None:
    sandbox, _ = await _running_machine(sandbox_uow_factory)

    assert await _still_running(sandbox_uow_factory, sandbox.id)


async def test_a_machine_released_since_the_meter_saw_it_is_left_asleep(
    sandbox_uow_factory,
) -> None:
    sandbox, instance = await _running_machine(sandbox_uow_factory)
    async with sandbox_uow_factory() as uow:
        repository = SandboxRepository(uow)
        await repository.mark_instance_released(instance.id)
        await repository.set_desired_state(sandbox.id, SandboxDesiredState.RELEASED)
        await uow.commit()

    assert not await _still_running(sandbox_uow_factory, sandbox.id)


async def test_a_machine_with_no_row_is_left_alone(sandbox_uow_factory) -> None:
    assert not await _still_running(sandbox_uow_factory, uuid4())
