"""The login survival rows only real E2B can answer.

Run with ``E2E_SANDBOX_MODE=e2b`` and the local-test team's ``E2B_API_KEY``;
skipped otherwise. Two rows:

- **A provider timeout.** E2B pauses a sandbox that reaches its timeout
  itself, with no clean close from Lemma first. Simulated by pausing through
  the SDK directly, the way that timeout does, then waking the machine.
- **The move to the default size.** On E2B the disk lives in the sandbox, so
  moving from 1x2 to 2x4 packs the home, starts a new sandbox and unpacks it
  -- the logins have to come through that, not just past it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.modules.workspace.domain.machine import MachineSize
from app.modules.workspace.infrastructure.models import SandboxModel
from app.modules.workspace.services.sandbox_composition import get_sandbox_service
from app.modules.workspace.services.size_migration import SizeMigration
from app.modules.workspace.tests.e2e.test_machine_login_survival_e2e import (
    _login,
    _machine,
    _sign_in,
    machine,  # noqa: F401 - the fixture, shared with the Docker matrix
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.workspace,
    pytest.mark.provider,
    pytest.mark.timeout(1800),
]


@pytest.fixture(autouse=True)
def _real_e2b_only() -> None:
    if settings.e2e_sandbox_mode != "e2b":
        pytest.skip("real E2B only: set E2E_SANDBOX_MODE=e2b and E2B_API_KEY")


async def test_a_provider_timeout_pause(machine) -> None:  # noqa: F811
    client = machine["client"]
    value = await _sign_in(client)

    service = get_sandbox_service()
    handle = await service.ensure(machine["id"])
    provider = service._provider
    sandbox = await provider._connect(handle.provider_id)
    await sandbox.pause(keep_memory=False, **provider._api())
    service.forget(machine["id"])

    assert await _login(client) == value


async def test_moving_from_the_old_size_carries_the_disk(machine) -> None:  # noqa: F811
    client = machine["client"]
    value = await _sign_in(client)
    async with machine["uow_factory"]() as uow:
        await uow.session.execute(
            update(SandboxModel)
            .where(SandboxModel.id == machine["id"])
            .values(size="1x2")
        )
        await uow.commit()

    moved = await SizeMigration(machine["uow_factory"]).migrate(
        await _machine(machine),
        to=MachineSize.parse("2x4"),
        acting_user=machine["user"],
    )

    assert moved is True
    assert (await client.get("/machines/me")).json()["size"] == "2x4"
    assert await _login(client) == value
