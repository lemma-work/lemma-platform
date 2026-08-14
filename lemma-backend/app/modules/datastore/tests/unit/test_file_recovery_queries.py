"""The scheduling sweeps must stay bounded and projected.

These run on crons — one every minute — against a table nothing prunes. An
unbounded or entity-hydrating sweep is not a slow query here; it is a worker
that dies once the backlog is large enough. Compiling the statement and
asserting its shape is the only way to catch a regression, since the service
tests mock the repository entirely.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.datastore.domain.file_entities import FileStatus
from app.modules.datastore.domain.file_projections import DispatchableFileRef
from app.modules.datastore.infrastructure.repositories.file_recovery_queries import (
    DatastoreFileRecoveryQueriesMixin,
)
from app.modules.test_support.mappers import configure_test_mappers

# Compiling a statement configures the mappers, and a partial model graph fails
# to resolve its relationship targets by name — so without this the file passes
# in a suite and fails on its own.
configure_test_mappers()


class _Row:
    def __init__(self, file_id, pod_id, status, metadata) -> None:
        self.id = file_id
        self.pod_id = pod_id
        self.status = status
        self.file_metadata = metadata


class _Result:
    def __init__(self, rows) -> None:
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        raise AssertionError(
            "scheduling sweeps must not hydrate DatastoreFile models — "
            "to_entity() reads every column, including last_processing_error"
        )


class _Session:
    def __init__(self, rows) -> None:
        self._rows = rows
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _Result(self._rows)


class _Repo(DatastoreFileRecoveryQueriesMixin):
    def __init__(self, rows) -> None:
        self.session = _Session(rows)


def _sql(session) -> str:
    assert session.statement is not None
    return str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


_NOW = datetime(2026, 8, 14, tzinfo=timezone.utc)


def _rows(count: int = 1):
    return [
        _Row(uuid4(), uuid4(), FileStatus.PENDING.value, {"k": "v"})
        for _ in range(count)
    ]


@pytest.mark.asyncio
async def test_dispatch_projects_four_columns_and_never_hydrates() -> None:
    repo = _Repo(_rows())

    result = await repo.list_pending_dispatch_candidates(
        per_pod_limit=5, global_limit=50
    )

    assert isinstance(result[0], DispatchableFileRef)
    sql = _sql(repo.session)
    selected = sql.partition(" \nFROM ")[0]
    assert "last_processing_error" not in selected
    assert "content_sha256" not in selected
    assert "description" not in selected


@pytest.mark.asyncio
async def test_dispatch_orders_by_rank_so_the_global_cap_stays_fair() -> None:
    """The window exists for round-robin; an unordered cap threw that away.

    With no ORDER BY before the limit, Postgres may return any rows satisfying
    the rank filter — typically in heap order — so whichever pods the plan
    emitted first took the whole batch. Rank-first ordering means every pod's
    oldest file is served before any pod's second.
    """
    repo = _Repo(_rows())

    await repo.list_pending_dispatch_candidates(per_pod_limit=5, global_limit=50)

    # rpartition, not partition: the window function has its own ORDER BY, and
    # the one that decides which rows survive the cap is the outermost.
    sql = _sql(repo.session).lower()
    outer_order = sql.rpartition("order by")[2]
    assert outer_order.index("rank") < outer_order.index("created_at")
    assert "limit 50" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    ["list_stale_recovery_candidates", "list_exhausted_recovery_candidates"],
)
async def test_recovery_sweeps_are_bounded_and_oldest_first(method) -> None:
    """Both sweeps were unbounded — the OOM shape, not the slow-query shape."""
    repo = _Repo(_rows(2))
    kwargs = {
        "processing_cutoff": _NOW - timedelta(minutes=35),
        "failed_cutoff": _NOW - timedelta(minutes=30),
        "limit": 250,
    }
    if method == "list_stale_recovery_candidates":
        kwargs["pending_cutoff"] = _NOW - timedelta(minutes=15)

    result = await getattr(repo, method)(**kwargs)

    assert all(isinstance(ref, DispatchableFileRef) for ref in result)
    sql = _sql(repo.session).lower()
    assert "limit 250" in sql
    assert "order by" in sql
    assert "updated_at asc" in sql
    assert "last_processing_error" not in sql.partition(" \nfrom ")[0]
