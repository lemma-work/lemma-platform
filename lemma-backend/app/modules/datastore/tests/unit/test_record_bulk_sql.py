"""Bulk statements have one SQL text per column set, whatever the row count.

A multi-row VALUES list named every cell, so each row count produced a new SQL
text -- each one cached by SQLAlchemy, up to ~15 MB apiece. The unnest form
binds one array per column instead; these pin that the text stays fixed and
that a column whose array type cannot be named keeps the per-row form.
"""

from __future__ import annotations

from uuid import uuid4

from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
    DatastoreTableEntity,
)
from app.modules.datastore.infrastructure.record_bulk_delete import (
    batch_bulk_deletes,
    prepare_bulk_deletes,
)
from app.modules.datastore.infrastructure.record_bulk_sql import (
    MAX_ROWS_PER_STATEMENT,
    chunk_rows,
)
from app.modules.datastore.infrastructure.record_bulk_update import (
    prepare_grouped_updates,
)
from app.modules.datastore.infrastructure.record_update_sql import (
    build_bulk_statements,
    bulk_conflict_clause,
)
from app.modules.datastore.services.table_context import TableContext


def _context(*, enable_rls: bool = False, extra=()) -> TableContext:
    table = DatastoreTableEntity(
        pod_id=uuid4(),
        table_name="tickets",
        primary_key_column="id",
        columns=[
            ColumnSchema(name="id", type=DatastoreDataType.UUID, auto=True),
            ColumnSchema(name="title", type=DatastoreDataType.TEXT),
            ColumnSchema(name="count", type=DatastoreDataType.INTEGER),
            ColumnSchema(name="score", type=DatastoreDataType.FLOAT),
            ColumnSchema(name="done", type=DatastoreDataType.BOOLEAN),
            ColumnSchema(name="meta", type=DatastoreDataType.JSON),
            ColumnSchema(name="due", type=DatastoreDataType.DATETIME),
            *extra,
        ],
        enable_rls=enable_rls,
    )
    return TableContext.from_table_entity(table, "pod_test", events_enabled=True)


_KEYS = ["id", "count", "done", "due", "meta", "score", "title"]


def _rows(count: int) -> list[dict]:
    return [{"id": uuid4(), "title": f"t{index}"} for index in range(count)]


def test_insert_sql_is_identical_for_10_and_1000_rows():
    ctx = _context()
    conflict = bulk_conflict_clause(ctx, _KEYS)

    small = build_bulk_statements(ctx, _KEYS, _rows(10), conflict)
    large = build_bulk_statements(ctx, _KEYS, _rows(1000), conflict)

    assert len(small) == len(large) == 1
    assert small[0][0] == large[0][0]
    sql = small[0][0]
    assert "unnest(CAST(:c0 AS uuid[]), CAST(:c1 AS integer[])" in sql
    assert "CAST(:c3 AS timestamptz[]), CAST(:c4 AS jsonb[])" in sql
    assert "CAST(:c5 AS numeric[]), CAST(:c6 AS text[])" in sql
    assert sql.index("ON CONFLICT") < sql.index("RETURNING *")
    # One bind per column, and an absent key is NULL as it always was.
    assert set(large[0][1]) == {f"c{index}" for index in range(len(_KEYS))}
    assert large[0][1]["c1"] == [None] * 1000


def test_a_column_without_a_nameable_array_type_keeps_the_per_row_form():
    ctx = _context(
        extra=(ColumnSchema(name="embedding", type=DatastoreDataType.VECTOR),)
    )
    statements = build_bulk_statements(
        ctx, ["id", "embedding"], [{"id": uuid4(), "embedding": [0.1]}], ""
    )
    assert ":r0_embedding" in statements[0][0]


def test_rows_are_chunked_by_the_row_budget_without_loss():
    rows = [{"a": index} for index in range(MAX_ROWS_PER_STATEMENT * 2 + 1)]
    chunks = chunk_rows(rows)
    assert [len(chunk) for chunk in chunks] == [
        MAX_ROWS_PER_STATEMENT,
        MAX_ROWS_PER_STATEMENT,
        1,
    ]


def test_update_sql_is_identical_for_10_and_1000_rows():
    ctx = _context(enable_rls=True)
    user_id = uuid4()

    def prepare(count: int):
        return prepare_grouped_updates(
            ctx,
            [(str(uuid4()), {"title": "x", "count": 1}) for _ in range(count)],
            user_id,
            enforce_user_scope=True,
            capture_previous=True,
        )

    small, large = prepare(10), prepare(1000)
    assert small is not None and large is not None
    assert small[0][0] == large[0][0]
    sql, params, changed, alias, expected = large[0]
    assert "FROM unnest(" in sql and "FOR UPDATE" in sql
    assert 't."user_id" = :current_user_id' in sql
    assert changed == ["count", "title"] and alias and expected == 1000
    assert params["current_user_id"] == str(user_id)


def test_update_groups_by_column_set_and_skips_no_op_rows():
    grouped = prepare_grouped_updates(
        _context(),
        [
            (str(uuid4()), {"title": "a"}),
            (str(uuid4()), {"count": 2}),
            (str(uuid4()), {"title": "b"}),
            (str(uuid4()), {}),
        ],
        uuid4(),
        enforce_user_scope=False,
        capture_previous=False,
    )
    assert grouped is not None
    assert sorted((changed, expected) for _, _, changed, _, expected in grouped) == [
        (["count"], 1),
        (["title"], 2),
    ]


def test_a_repeated_update_id_falls_back_to_per_row_statements():
    record_id = str(uuid4())
    grouped = prepare_grouped_updates(
        _context(),
        [(record_id, {"title": "a"}), (record_id, {"title": "b"})],
        uuid4(),
        enforce_user_scope=False,
        capture_previous=False,
    )
    assert grouped is None


def test_delete_sql_is_identical_for_10_and_1000_ids():
    ctx = _context(enable_rls=True)
    user_id = uuid4()

    def batch(count: int):
        prepared = prepare_bulk_deletes(
            ctx, [uuid4() for _ in range(count)], user_id, enforce_user_scope=True
        )
        return batch_bulk_deletes(ctx, prepared, user_id, enforce_user_scope=True)

    small, large = batch(10), batch(1000)
    assert small[0][1] == large[0][1]
    rows, sql, params = large[0]
    assert '"id" = ANY(CAST(:ids AS uuid[]))' in sql
    assert '"user_id" = :current_user_id' in sql
    assert len(rows) == len(params["ids"]) == 1000
