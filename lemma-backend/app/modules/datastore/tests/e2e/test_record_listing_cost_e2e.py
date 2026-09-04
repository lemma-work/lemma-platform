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

from uuid import uuid4

import pytest

from app.modules.datastore.tests.e2e.harness import DatastoreApi
from app.modules.test_support.query_counting import (
    counted_commits,
    counted_queries,
    format_statements,
)

pytestmark = pytest.mark.e2e


def _counts(statements: list[str], table: str) -> int:
    """COUNT(*) statements against *table* specifically.

    Named rather than counted globally: the shared counter observes every
    engine, so an unrelated COUNT elsewhere in the request would otherwise read
    as the per-page count this test exists to forbid.
    """
    return sum(
        1
        for statement in statements
        if "COUNT(*)" in statement.upper() and table in statement
    )


async def _seed(
    pod_api: DatastoreApi, table: str, rows: int, *, enable_rls: bool = False
) -> None:
    await pod_api.create_table(
        {
            "name": table,
            "enable_rls": enable_rls,
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

        with counted_queries() as statements:
            page = await pod_api.list_records(table, limit=50)

        assert page["total"] == 5, "total must stay exact, not become an estimate"
        assert page["next_page_token"] is None
        assert _counts(statements, table) == 0, (
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

    @pytest.mark.asyncio
    async def test_the_default_sort_is_total_not_merely_usually_stable(
        self, pod_api: DatastoreApi
    ):
        """The behavioural test above does not actually enforce this.

        Removing the primary-key tiebreak leaves it green: on a small table
        with one stable plan, PostgreSQL happens to return tied rows in the
        same order every time, so paging looks correct right up until the plan
        changes — a bigger table, a parallel scan, an index the planner
        suddenly prefers — and then rows repeat and vanish in production while
        every test still passes. Verified by reverting the tiebreak: the paging
        assertions above stayed green.

        What can be enforced is the invariant itself. A sort is only a stable
        page boundary if it is *total*, which means it has to end in a unique
        column. That is a property of the statement, so the statement is what
        this reads.
        """
        table = f"cost_total_{uuid4().hex[:8]}"
        await _seed(pod_api, table, 3)

        with counted_queries() as statements:
            await pod_api.list_records(table, limit=2)

        listings = [
            statement
            for statement in statements
            if "ORDER BY" in statement and table in statement
        ]
        assert listings, (
            "no ORDER BY reached the database for a record listing, so this "
            f"test read nothing:\n{format_statements(statements)}"
        )
        for statement in listings:
            order_by = statement.split("ORDER BY", 1)[1]
            assert "created_at" in order_by and "id" in order_by, (
                "the default listing sort does not end in a unique column, so "
                "rows that tie on created_at have no defined order between "
                f"pages and can repeat or disappear:\n  ORDER BY{order_by[:120]}"
            )


class TestBulkUpdateCost:
    @pytest.mark.asyncio
    async def test_a_bulk_update_is_one_transaction_not_one_per_row(
        self, pod_api: DatastoreApi
    ):
        """`update_record` is right for one row and wrong N times.

        It opens its own session, sets the RLS context, runs the UPDATE, stages
        its event and commits — so a bulk update paid four round trips and a
        fresh connection checkout per row, while the create and upsert paths
        had been batching all along.
        """
        table = f"cost_bulk_{uuid4().hex[:8]}"
        # RLS on, deliberately. The non-RLS seed used elsewhere in this file
        # skips the `set_rls_context` call entirely, which made the RLS
        # assertion below unable to fail.
        await _seed(pod_api, table, 20, enable_rls=True)
        rows = (await pod_api.list_records(table, limit=20))["items"]

        with counted_commits() as few, counted_queries() as few_statements:
            await pod_api.bulk_update(
                table, [{"id": rows[0]["id"], "title": "renamed-0"}]
            )
        with counted_commits() as many, counted_queries() as many_statements:
            updated = await pod_api.bulk_update(
                table,
                [
                    {"id": row["id"], "title": f"again-{index}"}
                    for index, row in enumerate(rows)
                ],
            )

        assert updated["count"] == 20

        # Counted through SQLAlchemy's `commit` event, not by grepping the
        # statement log. A commit goes through the dialect's transaction API and
        # never appears as a cursor execute, so the previous
        # `s.strip() == "COMMIT"` test counted zero however many transactions
        # ran -- an assertion that could not fail, and did not when a per-row
        # commit was reintroduced to check.
        #
        # Compared against a one-row update rather than pinned to a number. A
        # bulk update legitimately spans more than one transaction (the
        # application connection is released before the datastore work begins,
        # and the event outbox is ensured), and those are properties of the
        # request, not of the batch. What must not happen is the count moving
        # with the number of rows.
        assert many.count("COMMIT") == few.count("COMMIT"), (
            f"{many.count('COMMIT')} commits for 20 rows against "
            f"{few.count('COMMIT')} for one; the transaction count is scaling "
            "with the batch, which is the per-row path returning"
        )
        rls_many = sum(1 for s in many_statements if "set_config" in s.lower())
        rls_few = sum(1 for s in few_statements if "set_config" in s.lower())
        assert rls_many == rls_few, (
            f"{rls_many} RLS context statements for 20 rows against {rls_few} "
            "for one; the context is being re-established per record"
        )
        assert rls_few > 0, (
            "no RLS context statement was issued at all, so this assertion "
            "cannot fail -- is the fixture table still RLS-enabled?"
        )

    @pytest.mark.asyncio
    async def test_a_bulk_update_that_fails_writes_nothing(self, pod_api: DatastoreApi):
        """The atomicity half, which matters more than the latency.

        Per-row commits meant a batch failing halfway left the first half
        written and the caller holding an error, with no way to tell which rows
        had landed.
        """
        table = f"cost_atomic_{uuid4().hex[:8]}"
        await _seed(pod_api, table, 4)
        rows = (await pod_api.list_records(table, limit=4))["items"]

        await pod_api.bulk_update(
            table,
            [
                {"id": rows[0]["id"], "title": "written"},
                {"id": str(uuid4()), "title": "no such row"},
            ],
            expected_status=404,
        )

        after = (await pod_api.list_records(table, limit=4))["items"]
        titles = {row["title"] for row in after}
        assert "written" not in titles, (
            "the first row was committed before the batch failed"
        )
