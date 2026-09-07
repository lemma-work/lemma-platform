"""The batched limit reads must return exactly what the serial ones did.

``get_usage_limits`` runs on the spending path, so the two properties that
matter are opposite in kind and both asserted here against a real Postgres:
the answers must be *identical* to the per-window reads they replace, and there
must be *fewer* statements. Either one alone is satisfiable by a bug -- a
batched read that returns zeros is fast and wrong, and an unchanged
implementation is correct and slow.

Real rows rather than fakes, deliberately. What changed is SQL: a ``FILTER``
per window over one scan, and an OR of exact scope tuples grouped by window
kind. A fake repository cannot disagree with the aggregate it is standing in
for, so it cannot fail the way this can.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.manager import DatabaseManager
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.test_support.query_counting import (
    counted_queries,
    format_statements,
    statements_touching,
)
from app.modules.usage.infrastructure.models import UsageLimitCounter, UsageRecord
from app.modules.usage.infrastructure.repositories import UsageRepository

pytestmark = pytest.mark.e2e


def _record(
    *,
    organization_id: UUID | None,
    user_id: UUID,
    occurred_at: datetime,
    cost_usd: float | None,
    profile_scope: str = "SYSTEM",
) -> UsageRecord:
    return UsageRecord(
        organization_id=organization_id,
        user_id=user_id,
        source_type="AGENT_RUN",
        profile_id="system:lemma",
        profile_scope=profile_scope,
        model_name="test-model",
        usage_kind="llm",
        input_tokens=10,
        output_tokens=5,
        units=0.0,
        cost_usd=cost_usd,
        occurred_at=occurred_at,
    )


async def _seed(
    session: AsyncSession, *, user_id: UUID, org_id: UUID, other_org_id: UUID
) -> dict[str, datetime]:
    """Rows spanning both windows, plus every row the filters must exclude."""
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Deliberately before the month start, so the two windows genuinely differ
    # and a single shared scan boundary would be visible if it were wrong.
    week_start = month_start - timedelta(days=3)

    inside_both = month_start + timedelta(hours=1)
    inside_week_only = week_start + timedelta(hours=1)

    session.add_all(
        [
            _record(
                organization_id=org_id,
                user_id=user_id,
                occurred_at=inside_both,
                cost_usd=1.25,
            ),
            _record(
                organization_id=org_id,
                user_id=user_id,
                occurred_at=inside_week_only,
                cost_usd=0.50,
            ),
            # Another user in the same org: counts toward the org total only.
            _record(
                organization_id=org_id,
                user_id=uuid4(),
                occurred_at=inside_both,
                cost_usd=4.00,
            ),
            # A different org: excluded when the user's limit is global with
            # exclusions, counted when it is not.
            _record(
                organization_id=other_org_id,
                user_id=user_id,
                occurred_at=inside_both,
                cost_usd=8.00,
            ),
            # Not a SYSTEM profile, and an unpriced row: both must be ignored.
            _record(
                organization_id=org_id,
                user_id=user_id,
                occurred_at=inside_both,
                cost_usd=99.0,
                profile_scope="BYOK",
            ),
            _record(
                organization_id=org_id,
                user_id=user_id,
                occurred_at=inside_both,
                cost_usd=None,
            ),
            # Before every window: must never be counted.
            _record(
                organization_id=org_id,
                user_id=user_id,
                occurred_at=week_start - timedelta(days=40),
                cost_usd=77.0,
            ),
        ]
    )
    session.add_all(
        [
            UsageLimitCounter(
                organization_id=org_id,
                user_id=user_id,
                window_kind="user_week",
                window_start=week_start,
                window_end=week_start + timedelta(days=7),
                used_usd=0.0,
                reserved_usd=0.75,
            ),
            UsageLimitCounter(
                organization_id=org_id,
                user_id=user_id,
                window_kind="user_month",
                window_start=month_start,
                window_end=month_start + timedelta(days=30),
                used_usd=0.0,
                reserved_usd=1.50,
            ),
            UsageLimitCounter(
                organization_id=org_id,
                user_id=None,
                window_kind="org_month",
                window_start=month_start,
                window_end=month_start + timedelta(days=30),
                used_usd=0.0,
                reserved_usd=3.25,
            ),
        ]
    )
    await session.flush()
    return {
        "now": now,
        "month_start": month_start,
        "week_start": week_start,
    }


@pytest.mark.parametrize("exclusions", [(), ("other",)])
async def test_batched_window_costs_equal_the_per_window_reads(
    db_manager: DatabaseManager, exclusions: tuple[str, ...]
) -> None:
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    user_id, org_id, other_org_id = uuid4(), uuid4(), uuid4()

    async with factory() as uow:
        repo = UsageRepository(uow)
        windows = await _seed(
            uow.session, user_id=user_id, org_id=org_id, other_org_id=other_org_id
        )
        excluded = (other_org_id,) if exclusions else ()

        serial = {
            "user_week": await repo.get_system_cost(
                organization_id=None,
                user_id=user_id,
                start=windows["week_start"],
                end=windows["now"],
                exclude_organization_ids=excluded,
            ),
            "user_month": await repo.get_system_cost(
                organization_id=None,
                user_id=user_id,
                start=windows["month_start"],
                end=windows["now"],
                exclude_organization_ids=excluded,
            ),
        }
        with counted_queries() as statements:
            batched = await repo.get_system_cost_by_window(
                organization_id=None,
                user_id=user_id,
                window_starts={
                    "user_week": windows["week_start"],
                    "user_month": windows["month_start"],
                },
                end=windows["now"],
                exclude_organization_ids=excluded,
            )

    assert batched == serial, (
        f"the batched window read disagreed with the per-window reads: "
        f"{batched} vs {serial}"
    )
    assert serial["user_week"] > serial["user_month"], (
        "the fixture's week window does not reach further back than its month "
        "window, so this test cannot tell the two FILTERs apart"
    )
    reads = statements_touching(statements, "usage_records")
    assert len(reads) == 1, (
        f"two windows cost {len(reads)} scans of usage_records:\n"
        f"{format_statements(reads)}"
    )


async def test_batched_reserved_costs_equal_the_per_scope_reads(
    db_manager: DatabaseManager,
) -> None:
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    user_id, org_id, other_org_id = uuid4(), uuid4(), uuid4()

    async with factory() as uow:
        repo = UsageRepository(uow)
        windows = await _seed(
            uow.session, user_id=user_id, org_id=org_id, other_org_id=other_org_id
        )
        scopes: list[tuple[UUID | None, UUID | None, str, datetime]] = [
            (org_id, user_id, "user_week", windows["week_start"]),
            (org_id, user_id, "user_month", windows["month_start"]),
            (org_id, None, "org_month", windows["month_start"]),
        ]
        serial = {
            window_kind: await repo.get_reserved_cost(
                organization_id=organization_id,
                user_id=scope_user_id,
                window_kind=window_kind,
                window_start=window_start,
            )
            for organization_id, scope_user_id, window_kind, window_start in scopes
        }
        with counted_queries() as statements:
            batched = await repo.get_reserved_costs(scopes=scopes)

    assert batched == serial, (
        f"the batched reserved read disagreed with the per-scope reads: "
        f"{batched} vs {serial}"
    )
    assert serial == {"user_week": 0.75, "user_month": 1.50, "org_month": 3.25}, (
        f"the seeded counters did not come back as written: {serial}"
    )
    reads = statements_touching(statements, "usage_limit_counters")
    assert len(reads) == 1, (
        f"three scopes cost {len(reads)} reads of usage_limit_counters:\n"
        f"{format_statements(reads)}"
    )


async def test_a_scope_with_no_counter_row_reads_as_zero(
    db_manager: DatabaseManager,
) -> None:
    """An absent counter must be 0.0, not a missing key.

    ``get_usage_limits`` indexes the result directly, so a scope that has
    never been reserved against -- the common case on a user's first call in a
    window -- must not raise.
    """
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    async with factory() as uow:
        repo = UsageRepository(uow)
        month_start = datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        batched = await repo.get_reserved_costs(
            scopes=[
                (None, uuid4(), "user_week", month_start),
                (None, None, "org_month", month_start),
            ]
        )
    assert batched == {"user_week": 0.0, "org_month": 0.0}
