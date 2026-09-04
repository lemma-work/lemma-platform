"""Dynamic tables must be indexed for the way they are actually read.

Pod tables were created with nothing but a primary key. That reads as a speed
problem and is worse than one: ``guard_query_plan`` plans every ad-hoc
query and refuses any whose estimated cost exceeds the ceiling, and with no
index the planner costs a sequential scan — so past a certain size a
reasonable query stops working rather than merely slowing down.

Asserted against a real PostgreSQL catalog rather than by inspecting the SQL we
generate, because the thing that can go wrong is the database disagreeing with
us: a silently truncated identifier, a column that is not there, an index whose
shape does not match the ORDER BY it was built for.
"""

from __future__ import annotations


import pytest
from sqlalchemy import text

from app.modules.datastore.infrastructure.record_indexes import record_index_name
from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e


async def _indexes_on(session_factory, schema_name: str, table_name: str) -> dict:
    async with session_factory() as session:
        rows = await session.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = :schema AND tablename = :table"
            ),
            {"schema": schema_name, "table": table_name},
        )
        return dict(rows.all())


@pytest.fixture
def schema_manager():
    from app.modules.datastore.infrastructure.schema_manager import SchemaManager

    return SchemaManager()


async def test_a_new_rls_table_is_indexed_for_its_default_listing(
    pod_api: DatastoreApi, schema_manager
):
    """The index must lead with ``user_id`` and match the listing's ORDER BY."""
    await pod_api.create_table(
        {
            "name": "expenses",
            "enable_rls": True,
            "columns": [{"name": "merchant", "type": "TEXT", "required": True}],
        }
    )
    schema_name = schema_manager.get_schema_name(pod_api.pod_id)
    indexes = await _indexes_on(schema_manager.session_factory, schema_name, "expenses")

    name = record_index_name("expenses")
    assert name in indexes, (
        f"a new RLS table has no listing index; it has only {sorted(indexes)}"
    )
    definition = indexes[name]
    assert "user_id" in definition and "created_at DESC" in definition, (
        f"the listing index does not match the default sort: {definition}"
    )
    assert definition.index("user_id") < definition.index("created_at"), (
        f"an RLS listing filters user_id first, so it must lead: {definition}"
    )


async def test_a_new_non_rls_table_is_indexed_without_user_id(
    pod_api: DatastoreApi, schema_manager
):
    await pod_api.create_table(
        {
            "name": "projects",
            "enable_rls": False,
            "columns": [{"name": "name", "type": "TEXT", "required": True}],
        }
    )
    schema_name = schema_manager.get_schema_name(pod_api.pod_id)
    indexes = await _indexes_on(schema_manager.session_factory, schema_name, "projects")

    definition = indexes[record_index_name("projects")]
    assert "created_at DESC" in definition and "user_id" not in definition, (
        f"a non-RLS table must not index a column it does not filter: {definition}"
    )


async def test_toggling_rls_rebuilds_the_index_in_the_new_shape(
    pod_api: DatastoreApi, schema_manager
):
    """The toggle materializes ``user_id``, so it owns the reshape.

    Without this the tables that most need a user-leading index -- the ones
    that just acquired the column -- would be the ones without one.
    """
    await pod_api.create_table(
        {
            "name": "notes",
            "enable_rls": False,
            "columns": [{"name": "body", "type": "TEXT", "required": True}],
        }
    )
    schema_name = schema_manager.get_schema_name(pod_api.pod_id)
    name = record_index_name("notes")

    before = await _indexes_on(schema_manager.session_factory, schema_name, "notes")
    assert "user_id" not in before[name]

    await schema_manager.set_table_rls(pod_api.pod_id, "notes", True)
    after = await _indexes_on(schema_manager.session_factory, schema_name, "notes")
    assert "user_id" in after[name], (
        f"enabling RLS left the index in its old shape: {after[name]}"
    )
    assert len([n for n in after if n.startswith("ix_notes_")]) == 1, (
        f"the rebuild left more than one listing index behind: {sorted(after)}"
    )

    await schema_manager.set_table_rls(pod_api.pod_id, "notes", False)
    restored = await _indexes_on(schema_manager.session_factory, schema_name, "notes")
    assert "user_id" not in restored[name], (
        f"disabling RLS left user_id in the index: {restored[name]}"
    )


async def test_an_existing_table_gets_its_index_on_next_read(
    pod_api: DatastoreApi, schema_manager
):
    """The lazy backfill is the whole story for tables already out there.

    Dropping the index simulates a table created before this existed -- which
    is every table in every pod today, and the ones large enough to matter are
    exactly the ones no migration could safely rewrite.
    """
    await pod_api.create_table(
        {
            "name": "invoices",
            "enable_rls": True,
            "columns": [{"name": "amount", "type": "FLOAT", "required": True}],
        }
    )
    schema_name = schema_manager.get_schema_name(pod_api.pod_id)
    name = record_index_name("invoices")

    async with schema_manager.session_factory() as session:
        await session.execute(text(f'DROP INDEX "{schema_name}"."{name}"'))
        await session.commit()
    assert name not in await _indexes_on(
        schema_manager.session_factory, schema_name, "invoices"
    )

    # A fresh manager, because the live one has already memoised this table.
    from app.modules.datastore.infrastructure.schema_manager import SchemaManager

    await SchemaManager().ensure_record_index(
        schema_name,
        "invoices",
        primary_key_column="id",
        has_created_at=True,
        enable_rls=True,
    )

    rebuilt = await _indexes_on(schema_manager.session_factory, schema_name, "invoices")
    assert name in rebuilt, "the lazy backfill did not create the index a listing needs"


async def test_two_long_table_names_sharing_a_prefix_get_distinct_indexes(
    pod_api: DatastoreApi, schema_manager
):
    """PostgreSQL truncates identifiers at 63 bytes, and says nothing.

    Nothing in the datastore validates identifier length, so two long names
    sharing a prefix would have collided on one truncated index name and the
    second table's creation would have failed with a confusing "already
    exists". The digest is of the full name, so they cannot collide.
    """
    shared = "quarterly_regional_revenue_reconciliation_summary"
    first, second = f"{shared}_alpha", f"{shared}_beta"
    for name in (first, second):
        await pod_api.create_table(
            {
                "name": name,
                "enable_rls": False,
                "columns": [{"name": "value", "type": "FLOAT"}],
            }
        )

    assert record_index_name(first) != record_index_name(second)
    schema_name = schema_manager.get_schema_name(pod_api.pod_id)
    for table_name in (first, second):
        indexes = await _indexes_on(
            schema_manager.session_factory, schema_name, table_name
        )
        index_name = record_index_name(table_name)
        assert len(index_name.encode("utf-8")) <= 63
        assert index_name in indexes, (
            f"{table_name} lost its index to a name collision: {sorted(indexes)}"
        )


async def test_the_default_listing_uses_the_index_rather_than_a_seq_scan(
    pod_api: DatastoreApi, schema_manager, fixed_test_user
):
    """The point of the shape: the planner must actually pick it.

    An index that matches the ORDER BY on paper but not in the planner's view
    is pure write-side cost. ``enable_seqscan = off`` is not used here -- the
    question is what Postgres chooses, not what it can be forced into -- so
    this asserts on the plan for the exact statement record listing issues.
    """
    await pod_api.create_table(
        {
            "name": "events",
            "enable_rls": False,
            "columns": [{"name": "label", "type": "TEXT", "required": True}],
        }
    )
    await pod_api.bulk_create("events", [{"label": f"e{i}"} for i in range(200)])
    schema_name = schema_manager.get_schema_name(pod_api.pod_id)

    async with schema_manager.session_factory() as session:
        await session.execute(text(f'ANALYZE "{schema_name}"."events"'))
        plan = await session.execute(
            text(
                f'EXPLAIN (FORMAT JSON) SELECT * FROM "{schema_name}"."events" '
                'ORDER BY "created_at" DESC, "id" DESC LIMIT 20 OFFSET 0'
            )
        )
        plan_json = plan.scalar_one()

    rendered = str(plan_json)
    assert record_index_name("events") in rendered, (
        f"the default listing did not use the index built for it:\n{rendered}"
    )
