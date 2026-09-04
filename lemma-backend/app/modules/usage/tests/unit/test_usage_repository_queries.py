"""Usage reads must not scale with how much usage there has been.

``usage_records`` gains a row per model call and is deliberately never pruned —
the decision was to keep the history and make the queries cheap instead. So the
two things guarded here are that a summary aggregates in the database rather
than in Python, and that a listing is always bounded.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.usage.infrastructure.repositories import (
    MAX_USAGE_PAGE_SIZE,
    UsageRepository,
)


class _Result:
    def __init__(self, rows) -> None:
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        raise AssertionError(
            "the usage summary must aggregate in SQL — hydrating records is "
            "what made it scale with the size of the table"
        )


class _Session:
    """Returns a canned grouped result for each aggregate the summary issues."""

    def __init__(self, batches) -> None:
        self._batches = list(batches)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self._batches.pop(0) if self._batches else [])


class _Uow:
    def __init__(self, batches=()) -> None:
        self.session = _Session(batches)


def _bucket(key, *, inp, out, units=0.0, cost=0.0, count=1):
    return SimpleNamespace(
        key=key,
        input_tokens=inp,
        output_tokens=out,
        units=units,
        cost_usd=cost,
        record_count=count,
    )


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()


_START = datetime(2026, 7, 1, tzinfo=timezone.utc)
_END = _START + timedelta(days=30)


@pytest.mark.asyncio
async def test_summary_aggregates_in_sql_and_totals_match_the_buckets() -> None:
    by_profile = [_bucket("default", inp=100, out=40, units=1.0, cost=0.5, count=3)]
    by_model = [
        _bucket("claude-opus-5", inp=70, out=30, units=0.6, cost=0.4, count=2),
        _bucket("claude-haiku-4-5", inp=30, out=10, units=0.4, cost=0.1, count=1),
    ]
    by_kind = [_bucket("llm", inp=100, out=40, units=1.0, cost=0.5, count=3)]
    uow = _Uow([by_profile, by_model, by_kind])

    summary = await UsageRepository(uow).get_usage_summary(
        organization_id=uuid4(), start=_START, end=_END
    )

    # Three grouped aggregates, not one row-by-row scan.
    assert len(uow.session.statements) == 3
    for statement in uow.session.statements:
        sql = _sql(statement)
        assert "group by" in sql
        assert "sum(" in sql

    assert summary.total_input_tokens == 100
    assert summary.total_output_tokens == 40
    assert summary.total_tokens == 140
    assert summary.system_cost_usd == pytest.approx(0.5)
    assert summary.total_by_model["claude-opus-5"]["total_tokens"] == 100
    assert summary.total_by_model["claude-haiku-4-5"]["record_count"] == 1
    assert summary.total_by_kind["llm"]["input_tokens"] == 100


@pytest.mark.asyncio
async def test_an_unpriced_model_does_not_erase_the_cost_of_a_priced_one() -> None:
    """COALESCE, not bare SUM: a null cost must contribute zero, not null."""
    uow = _Uow([[], [], []])

    await UsageRepository(uow).get_usage_summary(
        organization_id=uuid4(), start=_START, end=_END
    )

    assert "coalesce" in _sql(uow.session.statements[0])


@pytest.mark.asyncio
async def test_an_empty_window_summarizes_to_zero_without_failing() -> None:
    uow = _Uow([[], [], []])

    summary = await UsageRepository(uow).get_usage_summary(
        organization_id=uuid4(), start=_START, end=_END
    )

    assert summary.total_tokens == 0
    assert summary.system_cost_usd == 0.0
    assert summary.total_by_model == {}


class _ListResult:
    def scalars(self):
        return SimpleNamespace(all=list)


class _ListSession:
    def __init__(self) -> None:
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _ListResult()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (None, MAX_USAGE_PAGE_SIZE),
        (0, MAX_USAGE_PAGE_SIZE),
        (10_000, MAX_USAGE_PAGE_SIZE),
        (25, 25),
    ],
)
async def test_a_usage_listing_is_always_bounded(requested, expected) -> None:
    """``limit`` used to be optional, which made "no limit" a supported request."""
    uow = SimpleNamespace(session=_ListSession())

    await UsageRepository(uow).list_usage(
        organization_id=uuid4(), start=_START, end=_END, limit=requested
    )

    assert f"limit {expected}" in _sql(uow.session.statements[0])
