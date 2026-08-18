"""Listing records must not scan the whole table to build one page.

Two defects sat next to each other in `list_records`.

`SELECT COUNT(*)` ran on **every** page request, over the whole of the user's
table, with no index behind it, purely to decide whether to emit a
`next_page_token`. Asking for one more row than the caller wanted answers that
question directly, and when the extra row does not come back `total` is
arithmetic rather than a query.

And the default sort was `created_at DESC` alone, which is non-deterministic
whenever two rows share a timestamp — which they routinely do, because a bulk
insert writes them in one transaction with one `now()`. Postgres may order tied
rows differently between the page-1 query and the page-2 query, so paging could
repeat a row on one page and drop another entirely.
"""

from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.modules.datastore.infrastructure.session import get_datastore_engine
from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e


@contextmanager
def _counted_statements():
    """Count statements on the datastore engine, which is where records live."""
    statements: list[str] = []
    engine = get_datastore_engine().sync_engine

    def before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before)


def _counts(statements: list[str]) -> int:
    return sum(1 for s in statements if "COUNT(*)" in s.upper())


async def _seed(pod_api: DatastoreApi, table: str, rows: int) -> None:
    await pod_api.create_table(
        {
            "name": table,
            "enable_rls": False,
            "columns": [{"name": "title", "type": "TEXT", "required": True}],
        }
    )
    await pod_api.bulk_create(
        table, [{"title": f"row-{index}"} for index in range(rows)]
    )


class TestRecordListingCost:
    @pytest.mark.asyncio
    async def test_a_page_that_ends_the_data_issues_no_count(
        self, pod_api: DatastoreApi
    ):
        """The common case — a listing that fits — should cost one statement."""
        table = f"cost_short_{uuid4().hex[:8]}"
        await _seed(pod_api, table, 5)

        with _counted_statements() as statements:
            page = await pod_api.list_records(table, limit=50)

        assert page["total"] == 5, "total must stay exact, not become an estimate"
        assert page["next_page_token"] is None
        assert _counts(statements) == 0, (
            "a page shorter than the limit already knows the total; the COUNT is "
            "a full table scan run for nothing"
        )

    @pytest.mark.asyncio
    async def test_a_full_page_still_reports_an_exact_total(
        self, pod_api: DatastoreApi
    ):
        """When there *is* more data, `total` is still the real total — the
        frontend renders it, so it may not degrade into a guess."""
        table = f"cost_full_{uuid4().hex[:8]}"
        await _seed(pod_api, table, 12)

        page = await pod_api.list_records(table, limit=5)

        assert page["total"] == 12
        assert len(page["items"]) == 5, "the probe row must not leak into the page"
        assert page["next_page_token"] is not None

    @pytest.mark.asyncio
    async def test_paging_tied_timestamps_neither_repeats_nor_drops_a_row(
        self, pod_api: DatastoreApi
    ):
        """A bulk insert writes every row with the same `now()`, so the default
        sort was ordering on a column where every value ties."""
        table = f"cost_ties_{uuid4().hex[:8]}"
        await _seed(pod_api, table, 30)

        seen: list[str] = []
        token = None
        for _ in range(10):
            page = await pod_api.list_records(table, limit=7, page_token=token)
            seen.extend(item["title"] for item in page["items"])
            token = page["next_page_token"]
            if token is None:
                break

        assert len(seen) == 30, f"paged {len(seen)} rows out of 30"
        assert len(set(seen)) == 30, "a row was returned on two different pages"
