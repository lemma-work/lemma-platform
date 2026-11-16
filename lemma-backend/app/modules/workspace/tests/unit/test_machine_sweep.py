"""The sweep: cost control first, every step on a budget, no step stops the rest."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.modules.workspace.services.machine_sweep import (
    SweepStep,
    provider_stopped,
    run_steps,
    sweep_steps,
)
from app.modules.workspace.services.sandbox_sweeper import SandboxSweeper


async def test_a_slow_or_failing_step_does_not_stop_the_ones_after_it() -> None:
    ran: list[str] = []

    async def slow() -> None:
        await asyncio.sleep(5)

    async def broken() -> None:
        raise ConnectionError("a machine that will not answer")

    async def fine() -> None:
        ran.append("fine")

    unfinished = await run_steps(
        [
            SweepStep("slow", timedelta(milliseconds=20), slow),
            SweepStep("broken", timedelta(seconds=5), broken),
            SweepStep("fine", timedelta(seconds=5), fine),
        ]
    )

    assert unfinished == ["slow", "broken"]
    assert ran == ["fine"]


def test_cost_control_runs_first_and_the_budgets_fit_the_cap() -> None:
    steps = sweep_steps(None, service=None, sweeper=None)  # type: ignore[arg-type]

    assert [step.name for step in steps][:3] == [
        "meter",
        "release_idle",
        "reclaim_orphans",
    ]
    assert sum((step.budget for step in steps), timedelta()) < timedelta(minutes=30)


async def test_a_paused_sandbox_is_reported_stopped_and_a_volume_is_not() -> None:
    paused, running, disk = uuid4(), uuid4(), uuid4()

    async def list_objects(*, deadline_at):
        return [
            SimpleNamespace(sandbox_id=paused, kind="container", running=False),
            SimpleNamespace(sandbox_id=running, kind="container", running=True),
            SimpleNamespace(sandbox_id=disk, kind="volume", running=False),
            SimpleNamespace(sandbox_id=None, kind="container", running=False),
        ]

    service = SimpleNamespace(_provider=SimpleNamespace(list_objects=list_objects))

    assert await provider_stopped(service) == frozenset({paused})


async def test_an_unlistable_provider_reports_nothing_stopped() -> None:
    async def list_objects(*, deadline_at):
        raise OSError("provider down")

    service = SimpleNamespace(_provider=SimpleNamespace(list_objects=list_objects))

    assert await provider_stopped(service) == frozenset()


async def test_an_idle_service_does_not_keep_its_machine_awake() -> None:
    idle_ids = {"svc"}

    async def idle(sandbox_id, process_id) -> bool:
        return process_id in idle_ids

    sweeper = SandboxSweeper(service=None, uow_factory=None, idle_service=idle)
    running = SimpleNamespace(exit_code=None, state="running")

    only_idle_service = [SimpleNamespace(process_id="svc", **vars(running))]
    with_work = [
        *only_idle_service,
        SimpleNamespace(process_id="build", **vars(running)),
    ]

    assert not await sweeper._any_working(uuid4(), only_idle_service)
    assert await sweeper._any_working(uuid4(), with_work)
