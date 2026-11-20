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
from app.modules.usage.domain.ports import UsageLimitValues
from app.modules.usage.services.usage_limit_provider import (
    configure_usage_limit_provider,
)
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
    allowance = _Allowance()
    configure_usage_limit_provider(lambda uow: allowance)
    try:
        yield {
            "client": authenticated_client,
            "uow_factory": SessionUnitOfWorkFactory(db_manager.session_factory),
            "machine_id": UUID(machine["id"]),
            "allowance": allowance,
        }
    finally:
        configure_usage_limit_provider(None)


class _Allowance:
    """The plan's compute allowance, supplied the way a billing provider does."""

    units: float | None = None

    async def resolve_limits(
        self, *, organization_id: UUID | None, user_id: UUID
    ) -> UsageLimitValues:
        del organization_id, user_id
        return UsageLimitValues(user_monthly_compute_units=self.units)


async def test_out_of_compute_refuses_new_work_then_releases_and_keeps_logins(
    metered,
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

    # A minute of the default 1x2 machine is 1/120 CU; the month allows less.
    metered["allowance"].units = 0.005
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
    metered["allowance"].units = None
    await _warm(client)
    login = await _finished(
        client, (await _start(client, f"cat '{PROFILE}/e2e-login'"))["id"]
    )
    assert login["exit_code"] == 0
    assert "still-signed-in" in login["text"]


async def test_a_released_machines_session_is_closed_and_not_metered_again(
    metered,
) -> None:
    from sqlalchemy import select

    from app.modules.workspace.infrastructure.machine_models import (
        MachineSessionModel,
    )

    metered["allowance"].units = None
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


async def _sessions(metered) -> list:
    from sqlalchemy import select

    from app.modules.workspace.infrastructure.machine_models import (
        MachineSessionModel,
    )

    async with metered["uow_factory"]() as uow:
        rows = await uow.session.scalars(
            select(MachineSessionModel)
            .where(MachineSessionModel.sandbox_id == metered["machine_id"])
            .order_by(MachineSessionModel.started_at)
        )
        return list(rows.all())


async def test_a_machine_the_provider_paused_is_not_billed_for_the_pause(
    metered,
) -> None:
    """E2B pauses a sandbox on its own timeout and the row still says READY.
    Reported stopped, its session ends where it was last metered."""
    metered["allowance"].units = None
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    await compute_meter.meter_running(metered["uow_factory"], now=now)

    paused = await compute_meter.meter_running(
        metered["uow_factory"],
        now=now + timedelta(hours=3),
        stopped=frozenset({metered["machine_id"]}),
    )

    assert metered["machine_id"] not in paused.running
    [session] = await _sessions(metered)
    assert session.ended_at is not None
    assert session.metered_until <= now + timedelta(seconds=1)


async def test_a_session_the_machine_restarted_past_is_ended_and_a_new_one_opened(
    metered,
) -> None:
    """A stop nothing closed: the instance came up again after the session's
    last metering. The gap is not billed, and the resume is noticed."""
    from sqlalchemy import update

    from app.modules.workspace.infrastructure.machine_models import (
        MachineSessionModel,
    )

    metered["allowance"].units = None
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    await compute_meter.meter_running(metered["uow_factory"], now=now)
    stale_until = datetime.now(timezone.utc) - timedelta(hours=5)
    async with metered["uow_factory"]() as uow:
        await uow.session.execute(
            update(MachineSessionModel)
            .where(MachineSessionModel.sandbox_id == metered["machine_id"])
            .values(started_at=stale_until, metered_until=stale_until)
        )
        await uow.commit()

    tick = await compute_meter.meter_running(
        metered["uow_factory"], now=now + timedelta(minutes=1)
    )

    assert metered["machine_id"] in tick.resumed
    old, new = await _sessions(metered)
    assert old.ended_at == stale_until
    assert new.ended_at is None and new.started_at >= stale_until


async def test_a_machine_has_at_most_one_open_session(metered) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.modules.workspace.infrastructure.machine_models import (
        MachineSessionModel,
    )

    metered["allowance"].units = None
    await compute_meter.meter_running(metered["uow_factory"])
    now = datetime.now(timezone.utc)

    with pytest.raises(IntegrityError):
        async with metered["uow_factory"]() as uow:
            uow.session.add(
                MachineSessionModel(
                    sandbox_id=metered["machine_id"],
                    size="1x2",
                    compute_units_per_hour=0.5,
                    billing_scope="user",
                    started_at=now,
                    metered_until=now,
                )
            )
            await uow.commit()
