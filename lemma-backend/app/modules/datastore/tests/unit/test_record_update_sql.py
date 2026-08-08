"""Pre-image capture on record updates.

The SQL itself is exercised end to end against Postgres; what is worth pinning
here is the part that is pure logic — that the extra RETURNING column cannot be
shadowed by a user-named column, and that the prior image is narrowed to the
columns the write touched.
"""

from __future__ import annotations

import json
from uuid import uuid4

from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
    DatastoreTableEntity,
)
from app.modules.datastore.infrastructure.record_update_sql import (
    bulk_returning_statement,
    chunk_for_parameter_limit,
    extract_previous_image,
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

    colliding = previous_image_alias(
        _context("status", "__lemma_previous")
    )
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
    previous = extract_previous_image(
        {"status": "pending"}, ["status"]
    )
    assert previous == {"status": "pending"}


def test_a_written_column_missing_from_the_prior_row_reads_as_null():
    """A column added by this very write has no prior value, and that is data."""
    previous = extract_previous_image(
        json.dumps({"status": "pending"}), ["status", "owner"]
    )
    assert previous == {"status": "pending", "owner": None}


def test_unusable_pre_image_degrades_to_none_rather_than_raising():
    assert extract_previous_image(None, ["status"]) is None
    assert (
        extract_previous_image("not json", ["status"]) is None
    )


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
