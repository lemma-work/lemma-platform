"""What a bulk delete asks the database for, before it asks for it.

The transaction behaviour is exercised against Postgres in the module e2e
suite; what is worth pinning here is the pure part — that a repeated id is one
delete rather than a row reported missing, that the row scope reaches the
statement, and that the refusal names the ids nothing matched.
"""

from __future__ import annotations

from uuid import uuid4

from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
    DatastoreTableEntity,
)
from app.modules.datastore.infrastructure.record_bulk_delete import (
    _missing_ids_message,
    prepare_bulk_deletes,
)
from app.modules.datastore.services.table_context import TableContext


def _context(*, enable_rls: bool) -> TableContext:
    table = DatastoreTableEntity(
        pod_id=uuid4(),
        table_name="tickets",
        primary_key_column="id",
        columns=[
            ColumnSchema(name="id", type=DatastoreDataType.UUID, auto=True),
            ColumnSchema(name="title", type=DatastoreDataType.TEXT),
        ],
        enable_rls=enable_rls,
    )
    return TableContext.from_table_entity(table, "pod_test", events_enabled=False)


def test_one_statement_per_row_targeting_the_primary_key():
    ids = [uuid4(), uuid4()]

    prepared = prepare_bulk_deletes(
        _context(enable_rls=False), list(ids), uuid4(), enforce_user_scope=False
    )

    assert [record_id for record_id, _, _ in prepared] == ids
    for _, sql, params in prepared:
        assert sql.startswith('DELETE FROM "pod_test"."tickets" WHERE "id" = :id')
        assert sql.endswith("RETURNING *")
        assert set(params) == {"id"}


def test_a_repeated_id_is_one_delete_not_a_missing_row():
    """Asking twice for the same row to be gone is not a request that failed.

    Without this the second statement matches nothing -- the row is already
    deleted inside this transaction -- and the batch would be refused for an
    id the caller did in fact own.
    """
    record_id = uuid4()

    prepared = prepare_bulk_deletes(
        _context(enable_rls=False),
        [record_id, record_id, record_id],
        uuid4(),
        enforce_user_scope=False,
    )

    assert len(prepared) == 1


def test_row_scope_is_carried_into_every_statement():
    user_id = uuid4()

    prepared = prepare_bulk_deletes(
        _context(enable_rls=True), [uuid4()], user_id, enforce_user_scope=True
    )

    _, sql, params = prepared[0]
    assert '"user_id" = :current_user_id' in sql
    assert params["current_user_id"] == str(user_id)


def test_the_refusal_names_the_ids_that_matched_nothing():
    missing = [f"row-{index}" for index in range(3)]

    message = _missing_ids_message(missing, requested=10)

    assert "3 of the 10" in message
    for record_id in missing:
        assert record_id in message


def test_the_refusal_summarises_rather_than_pasting_a_whole_batch_back():
    missing = [f"row-{index}" for index in range(9)]

    message = _missing_ids_message(missing, requested=9)

    assert "and 4 more" in message
    assert "row-8" not in message
