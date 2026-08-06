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
