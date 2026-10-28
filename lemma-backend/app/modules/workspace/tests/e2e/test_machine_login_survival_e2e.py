"""The login survival matrix, on a real Docker machine.

A person signs a site in once. Nothing short of their own say-so may lose it:
not the machine sleeping, not any cleanup an agent or the janitor runs, not a
fresh disk, not a move to a bigger size, not the browser being opened bare,
not twenty runs at once. Each row does one of those and then reads the login
back. The one row that is allowed to lose it -- a home reset with
`forget_logins` -- is here too, so the matrix proves the difference.

The login is a marker inside a part of the profile the backup carries
(`Local Storage`), so a row passes only if the profile itself survived or was
restored, never because some other copy of the file was left lying around.
(A provider timeout and the E2B disk move are the real-E2B lane's rows.)
"""

from __future__ import annotations

import asyncio
import subprocess
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.tests.e2e.test_machines_api_e2e import (
    _finished,
    _start,
    _warm,
)
from sandbox_runtime.paths import BROWSER_PROFILE

pytestmark = [pytest.mark.e2e, pytest.mark.workspace, pytest.mark.timeout(1200)]

LOGIN = f"{BROWSER_PROFILE}/Default/Local Storage/e2e-login"


async def _run(client, command: str) -> dict:
    return await _finished(client, (await _start(client, command))["id"])


async def _sign_in(client) -> str:
    value = f"signed-in-{uuid4().hex[:8]}"
    done = await _run(
        client, f'mkdir -p "$(dirname "{LOGIN}")" && echo {value} > "{LOGIN}"'
    )
    assert done["exit_code"] == 0, done["text"]
    return value


async def _login(client) -> str:
    await _warm(client)
    done = await _run(client, f'cat "{LOGIN}" 2>/dev/null || true')
    return done["text"].strip()


@pytest.fixture
async def machine(
    local_sandbox_server,
    backend_server,
    configure_workspace_api_url,
    authenticated_client,
    fixed_test_user,
    db_manager,
):
    del local_sandbox_server, backend_server, configure_workspace_api_url
    await _warm(authenticated_client)
    row = (await authenticated_client.get("/machines/me")).json()
    return {
        "client": authenticated_client,
        "id": UUID(row["id"]),
        "user": UUID(fixed_test_user["id"]),
        "uow_factory": SessionUnitOfWorkFactory(db_manager.session_factory),
    }


async def _machine(machine):
    from app.modules.workspace.infrastructure.machine_repository import (
        MachineRepository,
    )

    async with machine["uow_factory"]() as uow:
        return await MachineRepository(uow).get(machine["id"])


async def test_released_while_idle(machine) -> None:
    client = machine["client"]
    value = await _sign_in(client)

    released = await client.post("/machines/me:release")
    assert released.status_code < 300, released.text

    assert await _login(client) == value


@pytest.mark.parametrize(
    "scopes", [["processes"], ["runs"], ["caches"], ["docker"], ["browser-session"]]
)
async def test_every_cleanup_short_of_the_home(machine, scopes) -> None:
    client = machine["client"]
    value = await _sign_in(client)

    cleaned = await client.post("/machines/me:reset", json={"scopes": scopes})
    assert cleaned.status_code == status.HTTP_200_OK, cleaned.text

    assert await _login(client) == value


async def test_a_fresh_home_brings_the_logins_back(machine) -> None:
    client = machine["client"]
    value = await _sign_in(client)
    await _run(client, "echo scratch > ~/lemma/e2e-scratch")

    reset = await client.post(
        "/machines/me:reset", json={"scopes": ["home"], "confirm": True}
    )
    assert reset.status_code == status.HTTP_200_OK, reset.text
    assert reset.json()["results"][0]["detail"] == "logins restored"

    assert await _login(client) == value
    gone = await _run(client, "test -e ~/lemma/e2e-scratch; echo $?")
    assert gone["text"].strip().endswith("1"), "the home was not replaced"


async def test_a_fresh_home_that_forgets_logins_is_the_one_way_to_lose_them(
    machine,
) -> None:
    client = machine["client"]
    await _sign_in(client)

    reset = await client.post(
        "/machines/me:reset",
        json={"scopes": ["home"], "confirm": True, "forget_logins": True},
    )
    assert reset.status_code == status.HTTP_200_OK, reset.text

    assert await _login(client) == ""


async def test_moving_from_the_old_size_to_the_default(machine) -> None:
    from sqlalchemy import update

    from app.modules.workspace.domain.machine import MachineSize
    from app.modules.workspace.infrastructure.models import SandboxModel
    from app.modules.workspace.services.size_migration import SizeMigration

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


async def test_the_janitor_pruning_a_full_disk(machine, monkeypatch) -> None:
    from app.modules.workspace.services.disk_janitor import keep_room

    client = machine["client"]
    value = await _sign_in(client)
    # Every disk is "past the line", so every prune step runs.
    monkeypatch.setattr(workspace_settings, "machine_disk_prune_fraction", 0.0)
    monkeypatch.setattr(workspace_settings, "machine_disk_refuse_fraction", 1.01)

    report = await keep_room(machine["id"], acting_user=machine["user"])

    assert report is not None
    assert await _login(client) == value


async def test_the_browser_opened_bare_and_closed(machine) -> None:
    client = machine["client"]
    value = await _sign_in(client)

    opened = await _run(
        client,
        "agent-browser open about:blank >/dev/null 2>&1; "
        "agent-browser close --all >/dev/null 2>&1; echo done",
    )
    assert "done" in opened["text"]

    assert await _login(client) == value


async def test_twenty_runs_at_once(machine) -> None:
    client = machine["client"]
    value = await _sign_in(client)

    started = await asyncio.gather(
        *(
            _start(client, f"mkdir -p ~/lemma/par-{n} && sleep 1 && echo ok-{n}")
            for n in range(20)
        )
    )
    finished = await asyncio.gather(
        *(_finished(client, process["id"]) for process in started)
    )

    assert [f["exit_code"] for f in finished] == [0] * 20
    assert await _login(client) == value


async def test_a_disk_the_provider_lost_comes_back_with_its_logins(machine) -> None:
    """An expired pause, a deleted volume: the machine starts on a fresh disk,
    and the sweep that sees it start puts the newest login copy back."""
    from app.modules.workspace.services import compute_meter, machine_housekeeping
    from app.modules.workspace.services.sandbox_composition import (
        get_sandbox_service,
    )

    client = machine["client"]
    value = await _sign_in(client)
    released = await client.post("/machines/me:release")  # the clean-close copy
    assert released.status_code < 300, released.text

    # The provider loses it -- not Lemma deleting it, which marks the machine
    # gone: this container and the volumes it mounts, by exact name, and the
    # machine's row left as it was.
    service = get_sandbox_service()
    handle = await service.ensure(machine["id"])
    mounts = subprocess.run(
        [
            "docker",
            "inspect",
            "-f",
            "{{range .Mounts}}{{if .Name}}{{.Name}} {{end}}{{end}}",
            handle.provider_id,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    subprocess.run(["docker", "rm", "-f", "-v", handle.provider_id], check=True)
    if mounts:
        subprocess.run(["docker", "volume", "rm", "-f", *mounts], check=True)
    service.forget(machine["id"])
    await _warm(client)
    assert (await _run(client, f'cat "{LOGIN}" 2>/dev/null || true'))[
        "text"
    ].strip() == "", "the disk was not replaced"

    tick = await compute_meter.meter_running(machine["uow_factory"])
    assert machine["id"] in tick.resumed
    await machine_housekeeping.after_resume(machine["uow_factory"], tick.resumed)

    assert await _login(client) == value
