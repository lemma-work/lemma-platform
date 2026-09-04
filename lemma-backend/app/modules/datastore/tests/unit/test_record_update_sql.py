"""Pre-image capture on record updates.

The SQL itself is exercised end to end against Postgres; what is worth pinning
here is the part that is pure logic — that the extra RETURNING column cannot be
shadowed by a user-named column, and that the prior image is narrowed to the
columns the write touched.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.infrastructure.record_page import order_by_clause
from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
    DatastoreTableEntity,
)
from app.modules.datastore.infrastructure.record_update_sql import (
    build_bulk_statements,
    bulk_returning_statement,
    chunk_for_parameter_limit,
    extract_previous_image,
    order_bulk_keys,
    previous_image_alias,
)
from app.modules.datastore.services.table_context import TableContext


def _context(*column_names: str) -> TableContext:
    table = DatastoreTableEntity(
        pod_id=uuid4(),
        table_name="tickets",
        primary_key_column="id",
        columns=[
            ColumnSchema(name="id", type=DatastoreDataType.UUID, auto=True),
            *[
                ColumnSchema(name=name, type=DatastoreDataType.TEXT)
                for name in column_names
            ],
        ],
        enable_rls=False,
    )
    return TableContext.from_table_entity(table, "pod_test", events_enabled=True)


def test_alias_avoids_a_column_that_would_shadow_it():
    """Column names are user-chosen, so the obvious alias is not safe."""
    plain = previous_image_alias(_context("status"))
    assert plain == "__lemma_previous"

    colliding = previous_image_alias(_context("status", "__lemma_previous"))
    assert colliding != "__lemma_previous"
    assert colliding not in {"status", "__lemma_previous"}


def test_alias_keeps_growing_past_repeated_collisions():
    ctx = _context("__lemma_previous", "__lemma_previous_", "__lemma_previous__")
    alias = previous_image_alias(ctx)
    assert alias == "__lemma_previous___"


def test_previous_image_is_narrowed_to_the_written_columns():
    row = json.dumps(
        {"id": "rec_1", "status": "pending", "priority": "low", "notes": "unchanged"}
    )
    previous = extract_previous_image(row, ["status"])
    assert previous == {"status": "pending"}


def test_previous_image_accepts_an_already_decoded_row():
    """Whether jsonb arrives as text or a dict depends on the driver's codecs."""
    previous = extract_previous_image({"status": "pending"}, ["status"])
    assert previous == {"status": "pending"}


def test_a_written_column_missing_from_the_prior_row_reads_as_null():
    """A column added by this very write has no prior value, and that is data."""
    previous = extract_previous_image(
        json.dumps({"status": "pending"}), ["status", "owner"]
    )
    assert previous == {"status": "pending", "owner": None}


def test_unusable_pre_image_degrades_to_none_rather_than_raising():
    assert extract_previous_image(None, ["status"]) is None
    assert extract_previous_image("not json", ["status"]) is None


def test_chunking_keeps_every_statement_under_the_parameter_limit():
    """Postgres binds at most 65535 parameters per statement."""
    rows = [{"a": 1} for _ in range(50_000)]
    chunks = chunk_for_parameter_limit(rows, columns_per_row=4)

    assert sum(len(chunk) for chunk in chunks) == len(rows)  # nothing dropped
    for chunk in chunks:
        assert len(chunk) * 4 <= 65_535


def test_a_very_wide_row_still_yields_at_least_one_row_per_chunk():
    chunks = chunk_for_parameter_limit([{"a": 1}, {"a": 2}], columns_per_row=100_000)
    assert [len(chunk) for chunk in chunks] == [1, 1]


def test_no_rows_means_no_statements():
    assert chunk_for_parameter_limit([], columns_per_row=3) == []


def test_multi_row_insert_binds_each_row_under_its_own_parameter_names():
    ctx = _context("status")
    sql, params = bulk_returning_statement(
        ctx,
        ["id", "status"],
        [{"id": "a", "status": "new"}, {"id": "b", "status": "open"}],
        "",
    )

    assert sql.count("(:r0_id, :r0_status)") == 1
    assert sql.count("(:r1_id, :r1_status)") == 1
    assert sql.endswith("RETURNING *")
    # Distinct names per row: a shared name would silently write one row twice.
    assert params == {
        "r0_id": "a",
        "r0_status": "new",
        "r1_id": "b",
        "r1_status": "open",
    }


def test_the_conflict_clause_lands_before_returning():
    """RETURNING after ON CONFLICT is what makes upserted rows come back."""
    ctx = _context("status")
    sql, _ = bulk_returning_statement(
        ctx, ["id"], [{"id": "a"}], ' ON CONFLICT ("id") DO UPDATE SET "x" = 1'
    )
    assert sql.index("ON CONFLICT") < sql.index("RETURNING")


def _order_bulk_keys_as_it_was_inline(
    primary_key: str, all_keys: set[str]
) -> list[str]:
    """The loop `order_bulk_keys` replaced, kept verbatim as the oracle."""
    ordered_keys: list[str] = []
    if primary_key in all_keys:
        ordered_keys.append(primary_key)
    ordered_keys.extend(sorted(key for key in all_keys if key != primary_key))
    return ordered_keys


def test_order_bulk_keys_matches_the_loop_it_replaced():
    """Column order decides the generated SQL, so the extraction has to be exact.

    This moved out of `_bulk_write_records` to keep that file under the
    architecture ratchet. A refactor that silently reordered columns would still
    produce valid SQL and would corrupt every bulk write, so it is pinned
    against the original loop rather than against a hand-written expectation.
    """
    cases = [
        ("id", {"id", "name", "created_at"}),
        ("id", {"name", "created_at"}),  # primary key absent from the payload
        ("id", {"id"}),
        ("sku", {"sku", "a", "Z", "_x"}),  # non-"id" primary key, mixed case
        ("id", set()),
    ]
    for primary_key, all_keys in cases:
        assert order_bulk_keys(primary_key, all_keys) == (
            _order_bulk_keys_as_it_was_inline(primary_key, all_keys)
        ), f"order changed for {primary_key!r} over {sorted(all_keys)}"


def test_build_bulk_statements_covers_every_record_once():
    """One (sql, params) pair per chunk, and no record dropped or duplicated.

    The other half of the same extraction. Chunking exists because Postgres caps
    bind parameters per statement, so the failure this guards against is a large
    bulk write silently writing a subset.
    """
    ctx = _context("id", "name")
    ordered_keys = ["id", "name"]
    records = [{"id": index, "name": f"row-{index}"} for index in range(250)]

    statements = build_bulk_statements(ctx, ordered_keys, records, "")

    expected_chunks = list(chunk_for_parameter_limit(records, len(ordered_keys)))
    assert len(statements) == len(expected_chunks)
    assert [
        bulk_returning_statement(ctx, ordered_keys, chunk, "")
        for chunk in expected_chunks
    ] == statements
    total = sum(len(params) // len(ordered_keys) for _sql, params in statements)
    assert total == len(records), "a bulk write would have written a subset"


class TestListingOrderIsAlwaysTotal:
    """`PS-DATA-011`: paging an unchanging table returns every record once.

    Offset paging is only defined over a total order. Sorting by a column whose
    values repeat leaves the order among equal rows to the planner, which is
    free to place a row on two consecutive pages or on neither. The default
    sort has always appended the primary key for this reason; an explicit sort
    was passed through exactly as the caller wrote it.
    """

    def test_an_explicit_sort_gets_the_primary_key_as_a_tiebreak(self):
        clause = order_by_clause(_context("status"), [("status", "asc")])

        assert clause == '"status" ASC, "id" ASC'

    def test_the_tiebreak_follows_the_last_clause_s_direction(self):
        clause = order_by_clause(
            _context("status", "amount"), [("status", "asc"), ("amount", "desc")]
        )

        assert clause == '"status" ASC, "amount" DESC, "id" DESC'

    def test_a_sort_that_is_already_unique_is_left_alone(self):
        clause = order_by_clause(_context("status"), [("id", "desc")])

        assert clause == '"id" DESC'

    def test_the_default_sort_keeps_the_tiebreak_it_always_had(self):
        assert (
            order_by_clause(_context("created_at"), None)
            == '"created_at" DESC, "id" DESC'
        )

    def test_a_table_without_created_at_orders_by_its_primary_key(self):
        assert order_by_clause(_context("status"), None) == '"id" DESC'

    def test_a_sort_column_is_still_validated_as_an_identifier(self):
        with pytest.raises(DatastoreValidationError):
            order_by_clause(_context("status"), [('status" DESC, (SELECT 1)', "asc")])
