"""Running out of compute, end to end, on a real Docker machine.

The month's allowance used up: the meter has written the machine's time, a new
process is refused with the reason and the reset date, the work already
running is left its grace and then the machine is released -- and the
browser's logins are still on the disk when the person comes back with
compute to spend. The meter and the sweep are called the way the worker's
tick calls them, with the clock moved instead of waited on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi import status

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.usage.config import usage_settings
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.services import compute_meter
from sandbox_runtime.paths import BROWSER_PROFILE
from app.modules.workspace.tests.e2e.test_machines_api_e2e import (
    _finished,
    _start,
    _warm,
)

pytestmark = [pytest.mark.e2e, pytest.mark.workspace, pytest.mark.timeout(900)]

PROFILE = f"{BROWSER_PROFILE}/Default/Local Storage"


@pytest.fixture
async def metered(
    monkeypatch,
    db_manager,
    local_sandbox_server,
    backend_server,
    configure_workspace_api_url,
    authenticated_client,
):
    del local_sandbox_server, backend_server, configure_workspace_api_url
    monkeypatch.setenv("COMPUTE_BILLING_ENABLED", "true")
    monkeypatch.setattr(workspace_settings, "compute_billing_enabled", True)
    await _warm(authenticated_client)
    machine = (await authenticated_client.get("/machines/me")).json()
    return {
        "client": authenticated_client,
        "uow_factory": SessionUnitOfWorkFactory(db_manager.session_factory),
        "machine_id": UUID(machine["id"]),
    }


def _allowance(monkeypatch, units: float | None) -> None:
    monkeypatch.setattr(usage_settings, "usage_user_monthly_compute_units", units)


async def test_out_of_compute_refuses_new_work_then_releases_and_keeps_logins(
    monkeypatch, metered
) -> None:
    client = metered["client"]
    sign_in = await _finished(
        client,
        (
            await _start(
                client,
                f"mkdir -p '{PROFILE}' && echo still-signed-in > '{PROFILE}/e2e-login'",
            )
        )["id"],
    )
    assert sign_in["exit_code"] == 0

    # A minute of a 2x4 machine is 1/60 CU; the month allows a hundredth.
    _allowance(monkeypatch, 0.01)
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    tick = await compute_meter.meter_running(metered["uow_factory"], now=now)
    assert tick.written >= 1
    assert metered["machine_id"] in tick.running

    refused = await client.post("/machines/me/processes", json={"command": "true"})
    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text
    assert refused.json()["code"] == "MACHINE_COMPUTE_EXHAUSTED"
    assert "resets on" in refused.json()["message"]

    # Work already running is not cut off at once...
    assert await compute_meter.enforce(metered["uow_factory"], now=now) == []
    # ...but is, once the grace is over.
    released = await compute_meter.enforce(
        metered["uow_factory"], now=now + compute_meter.GRACE + timedelta(seconds=1)
    )
    assert metered["machine_id"] in released

    still_refused = await client.post(
        "/machines/me/processes", json={"command": "true"}
    )
    assert still_refused.status_code == status.HTTP_403_FORBIDDEN

    # Next month, or a bigger plan: the machine comes back with its logins.
    _allowance(monkeypatch, None)
    await _warm(client)
    login = await _finished(
        client, (await _start(client, f"cat '{PROFILE}/e2e-login'"))["id"]
    )
    assert login["exit_code"] == 0
    assert "still-signed-in" in login["text"]


async def test_a_released_machines_session_is_closed_and_not_metered_again(
    monkeypatch, metered
) -> None:
    from sqlalchemy import select

    from app.modules.workspace.infrastructure.machine_models import (
        MachineSessionModel,
    )

    _allowance(monkeypatch, None)
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    await compute_meter.meter_running(metered["uow_factory"], now=now)

    released = await metered["client"].post("/machines/me:release")
    assert released.status_code in (
        status.HTTP_200_OK,
        status.HTTP_202_ACCEPTED,
        status.HTTP_204_NO_CONTENT,
    ), released.text

    async def sessions() -> list[tuple[UUID, datetime | None, datetime]]:
        async with metered["uow_factory"]() as uow:
            rows = await uow.session.scalars(
                select(MachineSessionModel).where(
                    MachineSessionModel.sandbox_id == metered["machine_id"]
                )
            )
            return [(s.id, s.ended_at, s.metered_until) for s in rows.all()]

    closed = await sessions()
    assert closed and all(ended is not None for _, ended, _ in closed)

    # An hour later the meter runs again: a stopped machine gains no time.
    await compute_meter.meter_running(
        metered["uow_factory"], now=now + timedelta(hours=1)
    )
    assert await sessions() == closed
