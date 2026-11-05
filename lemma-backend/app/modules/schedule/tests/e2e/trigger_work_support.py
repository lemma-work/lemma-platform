"""Shared helpers for the schedule work-runtime e2e tests: real Postgres, real API."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import text

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import (
    WorkClass,
    install_work_limit_port,
    reset_work_limit_port,
)
from app.core.infrastructure.work.config import default_max_running
from app.core.infrastructure.work.limits import WorkLimits
from app.core.infrastructure.work.rates import Rate, default_rates
from app.core.infrastructure.work.testing import make_due, run_until_idle
from app.modules.schedule.domain.interfaces import FilterVerdict
from app.modules.schedule.domain.schedule import ScheduleEntity
from app.modules.schedule.services.fire_cap import DAILY_RATE
from app.modules.schedule.services.trigger_filter import ScheduleFilter
from app.modules.schedule.work.kinds import FilterEvent

#: A cron the minimum-interval policy accepts.
EVERY_QUARTER_HOUR = "*/15 * * * *"
EVERY_HALF_HOUR = "*/30 * * * *"


async def create_pod(client: AsyncClient, org_id: str) -> UUID:
    response = await client.post(
        "/pods",
        json={
            "name": f"Trigger Work {uuid4().hex[:6]}",
            "description": "schedule work-runtime e2e",
            "organization_id": org_id,
        },
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["id"])


async def create_time_schedule(
    client: AsyncClient,
    pod_id: UUID,
    *,
    cron: str | None = EVERY_QUARTER_HOUR,
    scheduled_at: datetime | None = None,
    max_concurrent_runs: int | None = None,
) -> dict[str, object]:
    config: dict[str, object] = {"cron": cron} if cron else {}
    if scheduled_at is not None:
        config = {"scheduled_at": scheduled_at.isoformat()}
    if max_concurrent_runs is not None:
        config["max_concurrent_runs"] = max_concurrent_runs
    response = await client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "schedule_type": "TIME",
            "agent_name": "POD_DEFAULT",
            "instruction": "Say hello.",
            "config": config,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@dataclass(frozen=True, slots=True)
class Item:
    id: UUID
    kind: str
    state: str
    ns: str
    payload: dict[str, object]
    dedup_key: str | None
    run_at: datetime
    rate_key: str | None


async def items(kind: str | None = None, *, state: str | None = None) -> list[Item]:
    query = (
        "SELECT id, kind, state, ns, payload, dedup_key, run_at, rate_key "
        "FROM work_items WHERE true"
    )
    params: dict[str, object] = {}
    if kind is not None:
        query += " AND kind = :kind"
        params["kind"] = kind
    if state is not None:
        query += " AND state = :state"
        params["state"] = state
    async with async_session_maker() as session:
        rows = (
            await session.execute(text(query + " ORDER BY run_at, id"), params)
        ).all()
    return [Item(*row) for row in rows]


async def schedule_row(schedule_id: UUID | str) -> Mapping[str, object]:
    async with async_session_maker() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT is_active, next_fire_at, paused_reason FROM schedules "
                        "WHERE id = :id"
                    ),
                    {"id": str(schedule_id)},
                )
            )
            .mappings()
            .one_or_none()
        )
    assert row is not None
    return dict(row)


async def runs(schedule_id: UUID | str) -> list[Mapping[str, object]]:
    async with async_session_maker() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT id, source_event_id, status, attempts, started_at, "
                        "target_run_id, target_outcome, error_type, metadata, llm_output "
                        "FROM schedule_runs WHERE schedule_id = :id ORDER BY created_at, id"
                    ),
                    {"id": str(schedule_id)},
                )
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


async def execute(sql: str, **params: object) -> None:
    async with async_session_maker() as session:
        await session.execute(text(sql), params)
        await session.commit()


async def scalar(sql: str, **params: object) -> object:
    async with async_session_maker() as session:
        return await session.scalar(text(sql), params)


async def run(*classes: WorkClass) -> int:
    return await run_until_idle(async_session_maker, classes=list(classes) or None)


async def fire_due(kind: str = "schedule.fire") -> int:
    """Pull ``kind``'s queued items to now and run the events class."""
    await make_due(async_session_maker, kind=kind)
    return await run(WorkClass.EVENTS)


class CountingFilter(ScheduleFilter):
    """The model, counted: how many times was it asked, and about what."""

    def __init__(self, *, proceed: bool = True) -> None:
        self.proceed = proceed
        self.asked: list[str] = []

    async def judge(
        self, schedule: ScheduleEntity, event: FilterEvent
    ) -> FilterVerdict:
        del schedule
        self.asked.append(event.source_event_id)
        return FilterVerdict(
            proceed=self.proceed,
            output={"should_proceed": self.proceed, "reason_code": "test"},
        )


@asynccontextmanager
async def daily_fire_cap(pod_id: UUID, limit: int) -> AsyncIterator[None]:
    """A pod's daily trigger ceiling of ``limit``, as the limit port states it.

    Written to the queue row for the fires about to happen, and stated by the
    port too: a runtime sharing the database refreshes a queue that has work
    from the port, and a ceiling written only to the row was reset to the
    default before the trigger reached it -- the trigger then never paused.
    """
    ns = f"pod:{pod_id}"

    class _Port:
        async def limits_for(self, name: str) -> WorkLimits:
            rates = default_rates()
            if name == ns:
                rates[DAILY_RATE] = Rate(limit, 86400)
            return WorkLimits(
                max_running={c: default_max_running(c) for c in WorkClass},
                rates=rates,
            )

    install_work_limit_port(_Port())
    try:
        await execute(
            "UPDATE work_queues SET rates = rates || CAST(:rate AS jsonb) WHERE ns = :ns",
            rate=f'{{"{DAILY_RATE}": {{"limit": {limit}, "per_seconds": 86400}}}}',
            ns=ns,
        )
        yield
    finally:
        reset_work_limit_port()
