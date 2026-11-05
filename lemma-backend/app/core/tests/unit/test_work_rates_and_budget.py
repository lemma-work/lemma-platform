"""Rate buckets in the claim walk, rate declarations, the connection budget and the directory seam."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.infrastructure.work import limits as work_limits
from app.core.infrastructure.work.claim import Candidate, take
from app.core.infrastructure.work.config import CLASS_DEFAULTS
from app.core.infrastructure.work.context import FastContext
from app.core.infrastructure.work.db import (
    DbBudget,
    NestedWorkSession,
    PoolTooSmall,
    WorkDb,
    check_fits,
)
from app.core.infrastructure.work.limits import (
    ConfiguredWorkLimitPort,
    parse_overrides,
    select_override,
)
from app.core.infrastructure.work.rates import (
    DEFAULT_RATES,
    BucketId,
    BucketState,
    Rate,
    RateUse,
    bucket_ids,
    parse_rates,
    wait_for_token,
)
from app.core.infrastructure.work.registry import WorkRegistry
from app.core.infrastructure.work.types import WorkClass, WorkMode
from app.core.infrastructure.work.writer import WorkWriter
from app.core.registry.assembly import configure_namespace_directory
from app.core.registry.contract import LemmaModule

HOURLY_10 = Rate(10, 3600)
POD = UUID("11111111-1111-1111-1111-111111111111")
_INTERACTIVE_CAP = CLASS_DEFAULTS[WorkClass.INTERACTIVE].max_running
ORG = UUID("22222222-2222-2222-2222-222222222222")
BUCKET = BucketId("email_out", "")


def _c(*buckets: BucketId, key: str | None = None) -> Candidate:
    return Candidate(uuid4(), "k", key, 1 if key else None, tuple(buckets))


# --- the walk with buckets --------------------------------------------------


def test_a_rated_candidate_takes_a_token_and_the_rest_wait_one_refill_apart() -> None:
    candidates = [_c(BUCKET) for _ in range(5)]
    walk = take(candidates, 10, {}, {BUCKET: BucketState(2.5, HOURLY_10)})

    assert walk.taken == [candidates[0].id, candidates[1].id]
    assert walk.spent == {BUCKET: 2}
    # 0.5 tokens left; the k-th waiting item needs k - 0.5 more, at 360 s each.
    waits = [walk.throttled[c.id] for c in candidates[2:]]
    assert waits == pytest.approx([180.0, 540.0, 900.0])


def test_a_throttled_candidate_does_not_block_unrated_work_behind_it() -> None:
    rated = [_c(BUCKET) for _ in range(3)]
    plain = [_c() for _ in range(3)]
    walk = take([*rated, *plain], 4, {}, {BUCKET: BucketState(0.0, HOURLY_10)})
    assert walk.taken == [c.id for c in plain]
    assert set(walk.throttled) == {c.id for c in rated}
    assert not walk.spent


def test_a_bucket_held_by_another_claim_is_skipped_without_moving_the_item() -> None:
    held, plain = _c(BUCKET), _c()
    walk = take([held, plain], 4, {}, {})
    assert walk.taken == [plain.id]
    assert walk.throttled == {}


def test_a_busy_key_is_not_a_reason_to_throttle_or_to_spend() -> None:
    keyed = _c(BUCKET, key="conv:1")
    walk = take([keyed], 4, {"conv:1": 0}, {BUCKET: BucketState(0.0, HOURLY_10)})
    assert walk.taken == [] and walk.throttled == {} and not walk.spent


def test_an_item_needing_two_buckets_waits_for_the_emptier_one() -> None:
    per_trigger = BucketId("llm_filter", "schedule:1")
    per_pod = BucketId("llm_filter_pod", "")
    item = _c(per_trigger, per_pod)
    walk = take(
        [item],
        4,
        {},
        {
            per_trigger: BucketState(0.0, Rate(60, 3600)),
            per_pod: BucketState(0.0, Rate(300, 3600)),
        },
    )
    assert walk.throttled[item.id] == pytest.approx(60.0)


def test_wait_for_token_is_owed_over_refill_rate() -> None:
    assert wait_for_token(1.0, Rate(600, 3600)) == pytest.approx(6.0)
    assert wait_for_token(-1.0, Rate(600, 3600)) == 0.0


def test_bucket_ids_key_per_key_uses_by_rate_key_then_concurrency_key() -> None:
    uses = (RateUse("llm_filter", per_key=True), RateUse("llm_filter_pod"))
    assert bucket_ids(uses, "schedule:1", "conv:9") == (
        BucketId("llm_filter", "schedule:1"),
        BucketId("llm_filter_pod", ""),
    )
    assert bucket_ids(uses, None, "conv:9")[0] == BucketId("llm_filter", "conv:9")


def test_parse_rates_refuses_zero_booleans_and_shapes_it_cannot_read() -> None:
    raw = {
        "ok": {"limit": 5, "per_seconds": 60},
        "zero": {"limit": 0, "per_seconds": 60},
        "bool": {"limit": True, "per_seconds": 60},
        "per": {"limit": 5, "per_seconds": 0},
        "shape": [1, 2],
    }
    assert parse_rates(raw) == (("ok", Rate(5, 60.0)),)


def test_the_decided_trigger_caps_are_the_defaults() -> None:
    assert DEFAULT_RATES["trigger_fire"] == Rate(600, 3600)
    assert DEFAULT_RATES["llm_filter"] == Rate(60, 3600)
    assert DEFAULT_RATES["llm_filter_pod"] == Rate(300, 3600)


async def test_an_override_replaces_one_rate_and_keeps_the_other_defaults() -> None:
    raw = json.dumps(
        [{"pod_id": str(POD), "rates": {"llm_filter": {"limit": 6, "per_seconds": 60}}}]
    )
    limits = await ConfiguredWorkLimitPort(raw_overrides=raw).limits_for(f"pod:{POD}")
    assert limits.rates["llm_filter"] == Rate(6, 60.0)
    assert limits.rates["trigger_fire"] == DEFAULT_RATES["trigger_fire"]


def test_a_longer_slug_prefix_wins_however_long_both_are() -> None:
    rules = parse_overrides(
        json.dumps(
            [
                {"slug_prefix": "acme-production-eu", "max_running": {"events": 1}},
                {"slug_prefix": "acme-production-", "max_running": {"events": 2}},
            ]
        )
    )
    chosen = select_override(rules, f"pod:{POD}", (ORG, "acme-production-eu-west"))
    assert chosen is not None and chosen.value == "acme-production-eu"


# --- declaring rates --------------------------------------------------------


class _P(BaseModel):
    pass


def test_a_rate_with_no_default_is_refused_at_registration() -> None:
    registry = WorkRegistry()
    with pytest.raises(ValueError, match="no default for rate 'made_up'"):

        @registry.handler(
            kind="k",
            cls=WorkClass.EVENTS,
            mode=WorkMode.FAST,
            payload=_P,
            rates=["made_up"],
        )
        async def handle(ctx: FastContext, payload: _P) -> None: ...


def test_a_rate_named_twice_is_refused() -> None:
    registry = WorkRegistry()
    with pytest.raises(ValueError, match="names a rate twice"):

        @registry.handler(
            kind="k",
            cls=WorkClass.EVENTS,
            mode=WorkMode.FAST,
            payload=_P,
            rates=["email_out", RateUse("email_out", per_key=True)],
        )
        async def handle(ctx: FastContext, payload: _P) -> None: ...


async def test_a_per_key_rate_needs_a_key_at_enqueue_and_a_key_needs_a_per_key_rate() -> (
    None
):
    registry = WorkRegistry()

    @registry.handler(
        kind="keyed",
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=_P,
        rates=[RateUse("trigger_fire", per_key=True)],
    )
    async def keyed(ctx: FastContext, payload: _P) -> None: ...

    @registry.handler(
        kind="plain", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=_P
    )
    async def plain(ctx: FastContext, payload: _P) -> None: ...

    # Refused before any statement runs, so no session is needed.
    writer = WorkWriter(session=None, registry=registry)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="pass rate_key or concurrency_key"):
        await writer.add("keyed", f"pod:{POD}")
    with pytest.raises(ValueError, match="declares no per-key rate"):
        await writer.add("plain", f"pod:{POD}", rate_key="schedule:1")


# --- the connection budget --------------------------------------------------


def test_the_budget_is_handlers_plus_a_claim_per_class_plus_control_plus_side() -> None:
    budget = DbBudget({WorkClass.EVENTS: 6, WorkClass.INGESTION: 2}, control=4)
    assert budget.total == 6 + 2 + 2 + 4 + 1


async def test_a_pool_smaller_than_the_budget_refuses_to_start() -> None:
    budget = DbBudget({WorkClass.EVENTS: 6}, control=4)
    small = create_async_engine(
        "postgresql+asyncpg://u:p@127.0.0.1:1/x", pool_size=5, max_overflow=0
    )
    exact = create_async_engine(
        "postgresql+asyncpg://u:p@127.0.0.1:1/x", pool_size=budget.total, max_overflow=0
    )
    try:
        with pytest.raises(PoolTooSmall, match="holds 5"):
            check_fits(budget, small)
        check_fits(budget, exact)
    finally:
        await small.dispose()
        await exact.dispose()


class _FakeSession:
    pass


def _fake_maker() -> object:
    @asynccontextmanager
    async def session() -> AsyncIterator[_FakeSession]:
        yield _FakeSession()

    return session


async def test_a_handler_session_inside_another_raises_instead_of_waiting() -> None:
    db = WorkDb(_fake_maker(), DbBudget({WorkClass.EVENTS: 1}, control=1))  # type: ignore[arg-type]
    async with db.handler(WorkClass.EVENTS):
        with pytest.raises(NestedWorkSession):
            async with db.handler(WorkClass.EVENTS):
                pass
        # Control and side sessions may nest under a handler; they draw on
        # their own slots.
        async with db.control():
            pass


async def test_handler_sessions_beyond_the_class_slots_wait_for_one_to_close() -> None:
    db = WorkDb(_fake_maker(), DbBudget({WorkClass.EVENTS: 2}, control=1))  # type: ignore[arg-type]
    open_now = 0
    peak = 0

    async def use() -> None:
        nonlocal open_now, peak
        async with db.handler(WorkClass.EVENTS):
            open_now += 1
            peak = max(peak, open_now)
            await asyncio.sleep(0.01)
            open_now -= 1

    await asyncio.gather(*(use() for _ in range(10)))
    assert peak == 2


# --- the namespace directory seam -------------------------------------------


class _Directory:
    async def organization_of(self, ns: str) -> tuple[UUID, str] | None:
        return (ORG, "acme") if ns == f"pod:{POD}" else None


def test_the_directory_a_module_declares_is_what_slug_rules_use() -> None:
    try:
        configure_namespace_directory(
            [
                LemmaModule(name="a"),
                LemmaModule(name="pod", namespace_directory=_Directory),
            ]
        )
        assert isinstance(work_limits.current_namespace_directory(), _Directory)
        with pytest.raises(RuntimeError, match="more than one module"):
            configure_namespace_directory(
                [
                    LemmaModule(name="a", namespace_directory=_Directory),
                    LemmaModule(name="b", namespace_directory=_Directory),
                ]
            )
    finally:
        work_limits.declare_namespace_directory(None)


async def test_slug_and_prefix_rules_reach_a_pod_through_the_declared_directory() -> (
    None
):
    raw = json.dumps(
        [
            {"slug_prefix": "ac", "max_running": {"events": 3}},
            {"slug": "acme", "max_running": {"interactive": 5}},
        ]
    )
    port = ConfiguredWorkLimitPort(raw_overrides=raw)
    assert (await port.limits_for(f"pod:{POD}")).cap(
        WorkClass.INTERACTIVE
    ) == _INTERACTIVE_CAP
    work_limits.declare_namespace_directory(_Directory())
    try:
        limits = await port.limits_for(f"pod:{POD}")
        assert limits.cap(WorkClass.INTERACTIVE) == 5  # slug beats prefix
        assert limits.cap(WorkClass.EVENTS) == 16
    finally:
        work_limits.declare_namespace_directory(None)
