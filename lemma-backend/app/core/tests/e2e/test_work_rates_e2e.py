"""Rate limits at claim, against real Postgres: exact under racing dispatchers, refill, no starvation."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from app.core.infrastructure.work.context import FastContext
from app.core.infrastructure.work.limits import (
    WorkLimits,
    install_work_limit_port,
    reset_work_limit_port,
)
from app.core.infrastructure.work.rates import Rate, RateUse, default_rates
from app.core.infrastructure.work.registry import WorkRegistry
from app.core.infrastructure.work.testing import dispatcher_for, run_until_idle
from app.core.infrastructure.work.types import WorkClass, WorkMode
from app.core.tests.e2e.work_support import POD_A, POD_B, Demo, WorkDb, write_effect
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

HOUR = 3600.0


class _Port:
    def __init__(self, rates: dict[str, Rate]) -> None:
        self.rates = rates

    async def limits_for(self, ns: str) -> WorkLimits:
        from app.core.infrastructure.work.config import default_max_running

        return WorkLimits(
            max_running={c: default_max_running(c) for c in WorkClass},
            rates={**default_rates(), **self.rates},
        )


@pytest.fixture
def limit_rates() -> Iterator[_Port]:
    port = _Port({})
    install_work_limit_port(port)
    try:
        yield port
    finally:
        reset_work_limit_port()


def _rated(
    registry: WorkRegistry, kind: str, cls: WorkClass, *uses: RateUse | str
) -> None:
    @registry.handler(kind=kind, cls=cls, mode=WorkMode.FAST, payload=Demo, rates=uses)
    async def handle(ctx: FastContext, payload: Demo) -> None:
        await write_effect(ctx.uow.session, kind, ctx.item.id)


async def test_racing_dispatchers_in_two_classes_spend_exactly_the_budget(
    work_db: WorkDb, registry: WorkRegistry, limit_rates: _Port
) -> None:
    """Two kinds in two classes share one pod's bucket; eight dispatchers race for it."""
    limit_rates.rates["email_out"] = Rate(10, HOUR)
    _rated(registry, "demo.mail_a", WorkClass.EVENTS, "email_out")
    _rated(registry, "demo.mail_b", WorkClass.DELIVERY, "email_out")
    for i in range(30):
        await work_db.enqueue(
            registry, "demo.mail_a" if i % 2 else "demo.mail_b", POD_A
        )

    dispatchers = [
        dispatcher_for(work_db.sessions, cls, registry=registry, owner=f"d{n}")
        for n in range(4)
        for cls in (WorkClass.EVENTS, WorkClass.DELIVERY)
    ]

    async def round_of(dispatcher: object) -> int:
        items = await dispatcher.claim_once()  # type: ignore[attr-defined]
        await asyncio.gather(*(dispatcher.execute(i) for i in items))  # type: ignore[attr-defined]
        return len(items)

    for _ in range(5):
        await asyncio.gather(*(round_of(d) for d in dispatchers))

    assert len(await work_db.effects()) == 10
    # Everything over the rate waits, uncharged, for its token.
    assert await work_db.count("state = 'queued'") == 20
    assert (
        await work_db.count(
            "attempts > 0 OR lease_losses > 0 OR error_class IS NOT NULL"
        )
        == 0
    )
    assert await work_db.count("state = 'queued' AND run_at <= now()") == 0
    tokens = await work_db.scalar(
        "SELECT tokens FROM work_rate_buckets WHERE ns = :ns AND name = 'email_out'",
        ns=POD_A,
    )
    assert float(tokens) == pytest.approx(0.0, abs=0.01)  # type: ignore[arg-type]


async def test_waiting_items_come_due_one_refill_apart(
    work_db: WorkDb, registry: WorkRegistry, limit_rates: _Port
) -> None:
    limit_rates.rates["email_out"] = Rate(2, HOUR)  # a token per 30 minutes
    _rated(registry, "demo.mail", WorkClass.EVENTS, "email_out")
    for _ in range(5):
        await work_db.enqueue(registry, "demo.mail", POD_A)

    assert await run_until_idle(work_db.sessions, registry=registry) == 2

    waits = sorted(
        float(w)  # type: ignore[arg-type]
        for w in [
            await work_db.scalar(
                f"SELECT EXTRACT(EPOCH FROM run_at - now()) FROM work_items "
                f"WHERE state = 'queued' ORDER BY run_at OFFSET {k} LIMIT 1"
            )
            for k in range(3)
        ]
    )
    assert waits == pytest.approx([1800, 3600, 5400], abs=5)


async def test_per_key_buckets_are_independent_and_each_spends_its_own(
    work_db: WorkDb, registry: WorkRegistry, limit_rates: _Port
) -> None:
    limit_rates.rates["trigger_fire"] = Rate(3, HOUR)
    _rated(
        registry, "demo.fire", WorkClass.EVENTS, RateUse("trigger_fire", per_key=True)
    )
    for schedule in ("schedule:1", "schedule:2"):
        for _ in range(5):
            await work_db.enqueue(registry, "demo.fire", POD_A, rate_key=schedule)

    assert await run_until_idle(work_db.sessions, registry=registry) == 6
    keys = await work_db.scalar(
        "SELECT string_agg(key || '=' || round(tokens)::text, ',' ORDER BY key) "
        "FROM work_rate_buckets"
    )
    assert keys == "schedule:1=0,schedule:2=0"


async def test_refill_follows_elapsed_time_and_stops_at_the_cap(
    work_db: WorkDb, registry: WorkRegistry, limit_rates: _Port
) -> None:
    limit_rates.rates["email_out"] = Rate(4, 4.0)  # a token a second, burst 4
    _rated(registry, "demo.mail", WorkClass.EVENTS, "email_out")
    for _ in range(12):
        await work_db.enqueue(registry, "demo.mail", POD_A)
    assert await run_until_idle(work_db.sessions, registry=registry) == 4

    async def rewind(seconds: float) -> None:
        await work_db.execute(
            "UPDATE work_rate_buckets SET updated_at = updated_at - "
            "make_interval(secs => :s)",
            s=seconds,
        )
        await work_db.execute(
            "UPDATE work_items SET run_at = now() WHERE state = 'queued'"
        )

    # 2.5 s of refill: two whole tokens and half of a third.
    await rewind(2.5)
    assert await run_until_idle(work_db.sessions, registry=registry) == 2
    # Plus whatever real time passed since the rewind, at a token a second.
    tokens = float(await work_db.scalar("SELECT tokens FROM work_rate_buckets"))  # type: ignore[arg-type]
    assert 0.5 <= tokens < 0.8

    # A long idle refills to the cap, not past it.
    await rewind(3600)
    assert await run_until_idle(work_db.sessions, registry=registry) == 4
    assert await work_db.count("state = 'queued'") == 2


async def test_a_rate_limited_backlog_starves_neither_other_kinds_nor_other_pods(
    work_db: WorkDb, registry: WorkRegistry, limit_rates: _Port
) -> None:
    limit_rates.rates["llm_filter_pod"] = Rate(1, HOUR)
    _rated(registry, "demo.filter", WorkClass.AUTOMATION, "llm_filter_pod")

    @registry.handler(
        kind="demo.plain", cls=WorkClass.AUTOMATION, mode=WorkMode.FAST, payload=Demo
    )
    async def plain(ctx: FastContext, payload: Demo) -> None:
        await write_effect(ctx.uow.session, "plain", ctx.item.id)

    for _ in range(50):
        await work_db.enqueue(registry, "demo.filter", POD_A)
    for _ in range(20):
        await work_db.enqueue(registry, "demo.plain", POD_A)
    for _ in range(3):
        await work_db.enqueue(registry, "demo.filter", POD_B)

    dispatcher = dispatcher_for(
        work_db.sessions, WorkClass.AUTOMATION, registry=registry, slots=4
    )
    for _ in range(12):
        items = await dispatcher.claim_once()
        await asyncio.gather(*(dispatcher.execute(i) for i in items))

    effects = await work_db.effects()
    assert effects.count("plain") == 20
    # One token per pod: POD_A's backlog does not spend POD_B's.
    assert effects.count("demo.filter") == 2
    assert await work_db.count("kind = 'demo.filter' AND ns = :ns", ns=POD_B) == 2


async def test_retention_drops_only_buckets_that_have_refilled(
    work_db: WorkDb, registry: WorkRegistry, limit_rates: _Port
) -> None:
    from app.core.infrastructure.work import maintenance

    limit_rates.rates["email_out"] = Rate(2, HOUR)
    _rated(registry, "demo.mail", WorkClass.EVENTS, "email_out")
    await work_db.enqueue(registry, "demo.mail", POD_A)
    await work_db.enqueue(registry, "demo.mail", POD_B)
    await run_until_idle(work_db.sessions, registry=registry)
    await work_db.execute(
        "UPDATE work_rate_buckets SET full_at = now() - interval '1 second' WHERE ns = :ns",
        ns=POD_B,
    )
    retention = WorkRegistry()
    retention.register(maintenance.registry.get("work.retention"))
    await work_db.enqueue(retention, "work.retention", "system")
    await run_until_idle(work_db.sessions, registry=retention)
    remaining = await work_db.scalar(
        "SELECT string_agg(ns, ',') FROM work_rate_buckets"
    )
    assert remaining == POD_A


async def test_a_throttled_item_runs_once_its_token_is_due(
    work_db: WorkDb, registry: WorkRegistry, limit_rates: _Port
) -> None:
    limit_rates.rates["email_out"] = Rate(1, 0.5)  # a token every half second
    _rated(registry, "demo.mail", WorkClass.EVENTS, "email_out")
    for _ in range(3):
        await work_db.enqueue(registry, "demo.mail", POD_A)

    async def run_due() -> int:
        await run_until_idle(work_db.sessions, registry=registry)
        return len(await work_db.effects())

    await eventually(
        label="throttled items run as their tokens refill",
        probe=run_due,
        done=lambda n: n == 3,
        timeout_seconds=10,
        interval_seconds=0.1,
    )
    assert await work_db.count("attempts > 0") == 0
