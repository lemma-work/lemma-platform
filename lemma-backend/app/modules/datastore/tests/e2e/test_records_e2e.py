"""E2E tests for datastore records: CRUD, bulk ops, SQL query, and RLS rows.

Record/query behaviour for the "project workspace" schema plus the
row-level-security (RLS) personal-rows semantics. Table schema/column
behaviour lives in ``test_tables_e2e.py``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import status
from httpx import AsyncClient

from app.modules.datastore.api.schemas.datastore_schemas import (
    MAX_BULK_RECORDS,
    MAX_RECORD_PAGE_SIZE,
)
from app.modules.datastore.tests.e2e.harness import (
    DatastoreApi,
    invite_to_pod,
    signup_user,
)
from app.modules.test_support.query_counting import (
    counted_queries,
    format_statements,
    statements_touching,
)

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def project_workspace(pod_api: DatastoreApi) -> DatastoreApi:
    """A pod with the projects/milestones/snapshots schema ready for records."""
    await pod_api.create_table(
        {
            "name": "projects",
            "enable_rls": False,
            "columns": [
                {"name": "name", "type": "TEXT", "required": True},
                {
                    "name": "status",
                    "type": "ENUM",
                    "required": True,
                    "options": ["planned", "active", "done"],
                },
                {"name": "budget", "type": "FLOAT"},
                {"name": "is_active", "type": "BOOLEAN"},
                {"name": "settings", "type": "JSON"},
                {"name": "owner_user", "type": "USER"},
                {"name": "artifact_path", "type": "FILE_PATH"},
            ],
            "config": {"label": "Projects"},
        }
    )
    await pod_api.add_column(
        "projects",
        {
            "name": "project_summary",
            "type": "TEXT",
            "computed": True,
            "expression": "name || ' [' || status || ']'",
        },
    )
    await pod_api.create_table(
        {
            "name": "milestones",
            "enable_rls": False,
            "primary_key_column": "id",
            "columns": [
                {"name": "id", "type": "SERIAL", "auto": True},
                {
                    "name": "project_id",
                    "type": "UUID",
                    "required": True,
                    "foreign_key": {"references": "projects.id"},
                },
                {"name": "title", "type": "TEXT", "required": True},
                {"name": "sort_order", "type": "INTEGER"},
            ],
        }
    )
    await pod_api.create_table(
        {
            "name": "snapshots",
            "enable_rls": False,
            "columns": [
                {"name": "label", "type": "TEXT", "required": True},
                {"name": "embedding", "type": "VECTOR"},
            ],
        }
    )
    return pod_api


async def _seed_projects(pod_api: DatastoreApi, owner_user_id: str) -> dict:
    """Create the two canonical project rows; returns {'apollo':..., 'beacon':...}."""
    apollo = await pod_api.create_record(
        "projects",
        {
            "name": "Apollo Rollout",
            "status": "active",
            "budget": 125000.5,
            "is_active": True,
            "settings": {"team": "platform"},
            "owner_user": owner_user_id,
            "artifact_path": "/briefs/apollo.md",
        },
    )
    beacon_id = str(uuid4())
    beacon = await pod_api.create_record(
        "projects",
        {
            "id": beacon_id,
            "name": "Beacon Migration",
            "status": "planned",
            "budget": 42000.0,
            "is_active": False,
            "settings": {"team": "ops"},
            "owner_user": owner_user_id,
            "artifact_path": "/briefs/beacon.md",
        },
    )
    assert beacon["id"] == beacon_id
    return {"apollo": apollo, "beacon": beacon}


class TestDatastoreRecords:
    @pytest.mark.asyncio
    async def test_records_coerce_typed_api_values_and_reject_malformed_values(
        self,
        pod_api: DatastoreApi,
    ):
        """HTTP record writes coerce supported JSON scalars into database types.

        Invalid temporal, numeric, and UUID inputs stay user-facing validation
        errors and never expose driver or SQL details.
        """
        await pod_api.create_table(
            {
                "name": "typed_events",
                "enable_rls": False,
                "columns": [
                    {"name": "happened_at", "type": "DATETIME", "required": True},
                    {"name": "due_on", "type": "DATE", "required": True},
                    {"name": "attempts", "type": "INTEGER", "required": True},
                    {"name": "score", "type": "FLOAT", "required": True},
                    {"name": "enabled", "type": "BOOLEAN", "required": True},
                    {"name": "reference_id", "type": "UUID", "required": True},
                    {"name": "payload", "type": "JSON", "required": True},
                ],
            }
        )
        reference_id = str(uuid4())
        created = await pod_api.create_record(
            "typed_events",
            {
                "happened_at": "2026-07-10T12:34:56Z",
                "due_on": "2026-07-11T08:00:00",
                "attempts": "7",
                "score": "98.25",
                "enabled": "yes",
                "reference_id": reference_id,
                "payload": {"source": "public-api", "retry": False},
            },
        )
        assert created["happened_at"].startswith("2026-07-10T12:34:56")
        assert created["due_on"] == "2026-07-11"
        assert created["attempts"] == 7
        assert created["score"] == 98.25
        assert created["enabled"] is True
        assert created["reference_id"] == reference_id
        assert created["payload"] == {"source": "public-api", "retry": False}

        updated = await pod_api.update_record(
            "typed_events",
            created["id"],
            {"attempts": 8, "score": 99, "enabled": 0},
        )
        assert updated["attempts"] == 8
        assert updated["score"] == 99.0
        assert updated["enabled"] is False

        invalid_values = (
            ("happened_at", "not-a-datetime"),
            ("due_on", "not-a-date"),
            ("attempts", "seven"),
            ("score", "high"),
            ("reference_id", "not-a-uuid"),
        )
        for field, value in invalid_values:
            response = await pod_api.request(
                "PATCH",
                (
                    f"/pods/{pod_api.pod_id}/datastore/tables/typed_events/"
                    f"records/{created['id']}"
                ),
                json={"data": {field: value}},
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST, response.text
            error = response.json()
            assert error["code"] == "DATASTORE_VALIDATION_ERROR"
            assert field in error["message"]
            assert "SQL" not in error["message"]

    @pytest.mark.asyncio
    async def test_records_support_typed_values_computed_columns_and_validation(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        """Records accept typed/explicit-id values, compute derived columns, and reject bad USER refs."""
        pod_api = project_workspace
        rows = await _seed_projects(pod_api, fixed_test_user["id"])
        assert rows["apollo"]["project_summary"] == "Apollo Rollout [active]"

        bad_user_ref = await pod_api.create_record(
            "projects",
            {
                "name": "Bad Reference",
                "status": "planned",
                "owner_user": str(uuid4()),
            },
            expected_status=status.HTTP_400_BAD_REQUEST,
        )
        assert bad_user_ref == {}

    @pytest.mark.asyncio
    async def test_records_can_be_filtered_sorted_and_paginated(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        """Listing projects supports JSON filter/sort and cursor pagination over budget."""
        pod_api = project_workspace
        rows = await _seed_projects(pod_api, fixed_test_user["id"])
        apollo, beacon = rows["apollo"], rows["beacon"]

        await pod_api.bulk_create(
            "milestones",
            [
                {"project_id": apollo["id"], "title": "Kickoff", "sort_order": 1},
                {"project_id": apollo["id"], "title": "Launch", "sort_order": 2},
                {"project_id": beacon["id"], "title": "Archive", "sort_order": 1},
            ],
        )
        await pod_api.bulk_create(
            "snapshots",
            [{"label": "baseline", "embedding": "[0.1, 0.2, 0.3]"}],
        )

        filtered = await pod_api.list_records(
            "projects",
            limit=20,
            filter='{"field":"budget","op":"gt","value":50000}',
            sort='{"field":"budget","direction":"desc"}',
        )
        assert [row["name"] for row in filtered["items"]] == ["Apollo Rollout"]

        first_page = await pod_api.list_records(
            "projects",
            limit=1,
            sort='{"field":"budget","direction":"desc"}',
        )
        assert first_page["next_page_token"]
        second_page = await pod_api.list_records(
            "projects",
            limit=1,
            sort='{"field":"budget","direction":"desc"}',
            page_token=first_page["next_page_token"],
        )
        assert [row["name"] for row in second_page["items"]] == ["Beacon Migration"]

    @pytest.mark.asyncio
    async def test_in_operator_matches_any_of_a_list(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        """The documented ``in`` operator filters on multiple values at once."""
        pod_api = project_workspace
        await _seed_projects(pod_api, fixed_test_user["id"])

        both = await pod_api.list_records(
            "projects",
            sort='{"field":"budget","direction":"desc"}',
            filter='{"field":"status","op":"in","value":["active","planned"]}',
        )
        assert [row["name"] for row in both["items"]] == [
            "Apollo Rollout",
            "Beacon Migration",
        ]

        one = await pod_api.list_records(
            "projects",
            filter='{"field":"name","op":"in","value":["Beacon Migration"]}',
        )
        assert [row["name"] for row in one["items"]] == ["Beacon Migration"]

        none = await pod_api.list_records(
            "projects",
            filter='{"field":"status","op":"in","value":[]}',
        )
        assert none["items"] == []

    @pytest.mark.asyncio
    async def test_query_returns_numeric_aggregates_as_numbers(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        """Postgres numerics (avg/sum) come back as JSON numbers, not strings."""
        pod_api = project_workspace
        await _seed_projects(pod_api, fixed_test_user["id"])

        result = await pod_api.query(
            "SELECT avg(budget) AS avg_budget, sum(budget) AS total_budget, "
            "count(*) AS n FROM projects"
        )
        row = result["items"][0]
        assert isinstance(row["avg_budget"], (int, float))
        assert not isinstance(row["avg_budget"], str)
        assert isinstance(row["total_budget"], (int, float))
        # (125000.5 + 42000.0) / 2 == 83500.25
        assert row["avg_budget"] == pytest.approx(83500.25)
        assert row["total_budget"] == pytest.approx(167000.5)
        assert row["n"] == 2

    @pytest.mark.asyncio
    async def test_record_can_be_fetched_and_updated_recomputing_columns(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        """Fetching then updating a record recomputes its computed summary column."""
        pod_api = project_workspace
        rows = await _seed_projects(pod_api, fixed_test_user["id"])
        apollo = rows["apollo"]

        got_apollo = await pod_api.get_record("projects", apollo["id"])
        assert got_apollo["name"] == "Apollo Rollout"
        updated_apollo = await pod_api.update_record(
            "projects",
            apollo["id"],
            {"status": "done", "is_active": False},
        )
        assert updated_apollo["project_summary"] == "Apollo Rollout [done]"

    @pytest.mark.asyncio
    async def test_sql_query_supports_joins_and_bulk_ops_but_blocks_mutations(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        """SQL query joins tables and feeds bulk update/delete, while DELETE statements are rejected."""
        pod_api = project_workspace
        rows = await _seed_projects(pod_api, fixed_test_user["id"])
        apollo, beacon = rows["apollo"], rows["beacon"]

        await pod_api.bulk_create(
            "milestones",
            [
                {"project_id": apollo["id"], "title": "Kickoff", "sort_order": 1},
                {"project_id": apollo["id"], "title": "Launch", "sort_order": 2},
                {"project_id": beacon["id"], "title": "Archive", "sort_order": 1},
            ],
        )

        milestones = await pod_api.query(
            "SELECT id, title, sort_order FROM milestones ORDER BY sort_order ASC"
        )
        milestone_ids_by_title = {
            row["title"]: row["id"] for row in milestones["items"]
        }
        await pod_api.bulk_update(
            "milestones",
            [
                {"id": milestone_ids_by_title["Kickoff"], "sort_order": 10},
                {"id": milestone_ids_by_title["Launch"], "sort_order": 20},
            ],
        )
        await pod_api.bulk_delete("milestones", [milestone_ids_by_title["Archive"]])

        joined = await pod_api.query(
            "SELECT p.name AS project_name, m.title "
            "FROM projects p JOIN milestones m ON m.project_id = p.id "
            "WHERE p.name = 'Apollo Rollout' ORDER BY m.sort_order ASC"
        )
        assert joined["items"] == [
            {"project_name": "Apollo Rollout", "title": "Kickoff"},
            {"project_name": "Apollo Rollout", "title": "Launch"},
        ]

        mutation = await pod_api.query(
            "DELETE FROM projects",
            expected_status=status.HTTP_400_BAD_REQUEST,
        )
        assert mutation["code"] == "DATASTORE_QUERY_ERROR"

    @pytest.mark.asyncio
    async def test_multi_table_query_resolves_every_table_in_one_read(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        """A join must not pay one table lookup per table it names.

        Resolving each referenced table on its own meant a query's fixed cost
        scaled with how many tables it joined -- a read, an authorization and a
        commit apiece -- before a single row was fetched. The lookup is now one
        statement; the per-table READ check still runs per table.

        Counted rather than timed, and compared against the same query naming a
        single table, so the assertion is about the *shape* of the work and not
        about how fast the container is.
        """
        pod_api = project_workspace
        rows = await _seed_projects(pod_api, fixed_test_user["id"])
        await pod_api.bulk_create(
            "milestones",
            [{"project_id": rows["apollo"]["id"], "title": "Kickoff", "sort_order": 1}],
        )

        with counted_queries() as single_statements:
            await pod_api.query("SELECT id FROM projects")
        with counted_queries() as join_statements:
            await pod_api.query(
                "SELECT p.name AS project_name, m.title "
                "FROM projects p JOIN milestones m ON m.project_id = p.id "
                "ORDER BY m.sort_order ASC"
            )

        single_lookups = statements_touching(single_statements, "datastore_tables")
        join_lookups = statements_touching(join_statements, "datastore_tables")
        assert single_lookups, (
            "the one-table query read no table metadata at all -- this test is "
            "no longer measuring the lookup it was written for"
        )
        assert len(join_lookups) <= len(single_lookups), (
            f"a two-table join issued {len(join_lookups)} table lookups where a "
            f"one-table query issued {len(single_lookups)}; the lookup is still "
            f"scaling with the number of tables:\n"
            f"{format_statements(join_lookups)}"
        )

    @pytest.mark.asyncio
    async def test_records_and_tables_can_be_deleted(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        """Records and a table can be deleted via bulk-delete, delete_record, and delete_table."""
        pod_api = project_workspace
        rows = await _seed_projects(pod_api, fixed_test_user["id"])
        apollo, beacon = rows["apollo"], rows["beacon"]

        await pod_api.bulk_create(
            "milestones",
            [
                {"project_id": apollo["id"], "title": "Kickoff", "sort_order": 1},
                {"project_id": beacon["id"], "title": "Archive", "sort_order": 1},
            ],
        )

        remaining_milestones = await pod_api.query("SELECT id FROM milestones")
        await pod_api.bulk_delete(
            "milestones",
            [row["id"] for row in remaining_milestones["items"]],
        )
        await pod_api.delete_record("projects", apollo["id"])
        await pod_api.delete_record("projects", beacon["id"])
        await pod_api.delete_table("snapshots")

    @pytest.mark.asyncio
    async def test_writing_to_reserved_system_table_is_forbidden(
        self,
        pod_api: DatastoreApi,
    ):
        """Writing to any ``reserved_*`` name is a 403. The controller's
        mutability guard fires on the name prefix before any table lookup, so this
        holds for reserved tables generically — no such table need exist."""
        reserved_write = await pod_api.request(
            "POST",
            f"/pods/{pod_api.pod_id}/datastore/tables/reserved_anything/records",
            json={"data": {"email": "blocked@example.com"}},
        )
        assert reserved_write.status_code == status.HTTP_403_FORBIDDEN


class TestDatastoreRlsRows:
    @pytest.mark.asyncio
    async def test_rls_table_behaves_like_personal_rows_for_each_user(
        self,
        pod_api: DatastoreApi,
        authenticated_client: AsyncClient,
        async_client: AsyncClient,
        fixed_test_org,
        member_users,
    ):
        """An RLS table isolates rows per user for read/get/update/SQL.

        A pod admin is scoped to their own rows by default too (so app apps keep
        per-user semantics); the full cross-user view is opt-in via ``mode=admin``,
        which a non-admin member cannot use.
        """
        await pod_api.create_table(
            {
                "name": "expenses",
                "enable_rls": True,
                "columns": [
                    {"name": "merchant", "type": "TEXT", "required": True},
                    {"name": "amount", "type": "FLOAT", "required": True},
                ],
            }
        )
        second_editor = await signup_user(async_client, "datastore-rls-editor-two")
        await invite_to_pod(
            authenticated_client,
            async_client,
            org_id=fixed_test_org["id"],
            pod_id=pod_api.pod_id,
            user=second_editor,
            role="POD_EDITOR",
        )
        editor_api = DatastoreApi(async_client, pod_api.pod_id, member_users["editor"])
        second_editor_api = DatastoreApi(async_client, pod_api.pod_id, second_editor)

        editor_row = await editor_api.create_record(
            "expenses",
            {"merchant": "Editor Lunch", "amount": 12.5},
        )
        second_editor_row = await second_editor_api.create_record(
            "expenses",
            {"merchant": "Second Editor Taxi", "amount": 31.0},
        )
        assert editor_row["user_id"] == member_users["editor"]["id"]
        assert second_editor_row["user_id"] == second_editor["id"]

        editor_list = await editor_api.list_records("expenses", limit=20)
        second_editor_list = await second_editor_api.list_records("expenses", limit=20)
        assert editor_list["items"] == [editor_row]
        assert second_editor_list["items"] == [second_editor_row]

        await editor_api.get_record(
            "expenses",
            second_editor_row["id"],
            expected_status=status.HTTP_404_NOT_FOUND,
        )
        editor_update_other = await editor_api.request(
            "PATCH",
            f"/pods/{pod_api.pod_id}/datastore/tables/expenses/records/{second_editor_row['id']}",
            json={"data": {"merchant": "nope"}},
        )
        assert editor_update_other.status_code == status.HTTP_404_NOT_FOUND

        # By default the pod admin is scoped to their OWN rows as well — they
        # created none, so the record list comes back empty. This is the per-user
        # view an app app relies on no matter who is signed in.
        admin_default_list = await pod_api.list_records(
            "expenses",
            limit=20,
            sort='{"field":"merchant","direction":"asc"}',
        )
        assert admin_default_list["items"] == []
        await pod_api.get_record(
            "expenses",
            second_editor_row["id"],
            expected_status=status.HTTP_404_NOT_FOUND,
        )

        # mode=admin opts the pod admin into the full, cross-user row set.
        admin_list = await pod_api.list_records(
            "expenses",
            limit=20,
            sort='{"field":"merchant","direction":"asc"}',
            mode="ADMIN",
        )
        assert admin_list["items"] == [editor_row, second_editor_row]
        admin_get_other = await pod_api.get_record(
            "expenses", second_editor_row["id"], mode="ADMIN"
        )
        assert admin_get_other["id"] == second_editor_row["id"]

        # A non-admin editor cannot escalate by asking for mode=admin.
        editor_admin_attempt = await editor_api.request(
            "GET",
            f"/pods/{pod_api.pod_id}/datastore/tables/expenses/records",
            params={"mode": "ADMIN"},
        )
        assert editor_admin_attempt.status_code == status.HTTP_403_FORBIDDEN

        # Ad-hoc SQL now runs against RLS tables, row-scoped to the caller.
        editor_sql = await editor_api.query("SELECT merchant FROM expenses")
        assert editor_sql["items"] == [{"merchant": "Editor Lunch"}]

        second_editor_sql = await second_editor_api.query(
            "SELECT merchant FROM expenses"
        )
        assert second_editor_sql["items"] == [{"merchant": "Second Editor Taxi"}]

        # SQL respects the same RLS contract as the record APIs: the pod admin is
        # scoped to their own rows by default (they own none), so the query is
        # empty until they explicitly ask for admin mode.
        admin_default_sql = await pod_api.query(
            "SELECT merchant FROM expenses ORDER BY merchant ASC"
        )
        assert admin_default_sql["items"] == []

        admin_sql = await pod_api.query(
            "SELECT merchant FROM expenses ORDER BY merchant ASC",
            mode="ADMIN",
        )
        assert [row["merchant"] for row in admin_sql["items"]] == [
            "Editor Lunch",
            "Second Editor Taxi",
        ]

        # A non-admin editor cannot escalate a SQL read via mode=admin.
        await editor_api.query(
            "SELECT merchant FROM expenses",
            mode="ADMIN",
            expected_status=status.HTTP_403_FORBIDDEN,
        )

    @pytest.mark.asyncio
    async def test_admin_mode_governs_cross_user_record_writes(
        self,
        pod_api: DatastoreApi,
        async_client: AsyncClient,
        member_users,
    ):
        """Admin mode lets a pod admin update/delete another member's RLS row; a non-admin is refused."""
        await pod_api.create_table(
            {
                "name": "expenses",
                "enable_rls": True,
                "columns": [
                    {"name": "merchant", "type": "TEXT", "required": True},
                    {"name": "amount", "type": "FLOAT", "required": True},
                ],
            }
        )
        editor_api = DatastoreApi(async_client, pod_api.pod_id, member_users["editor"])
        editor_row = await editor_api.create_record(
            "expenses", {"merchant": "Editor Lunch", "amount": 12.5}
        )

        # Default scope: the admin can't even see the editor's row, so the update 404s.
        await pod_api.update_record(
            "expenses",
            editor_row["id"],
            {"merchant": "nope"},
            expected_status=status.HTTP_404_NOT_FOUND,
        )

        # Admin mode lets the pod admin update another member's row.
        updated = await pod_api.update_record(
            "expenses",
            editor_row["id"],
            {"merchant": "Reconciled"},
            mode="ADMIN",
        )
        assert updated["merchant"] == "Reconciled"

        # A non-admin editor cannot wield admin mode for a cross-user bulk delete.
        await editor_api.bulk_delete(
            "expenses",
            [editor_row["id"]],
            mode="ADMIN",
            expected_status=status.HTTP_403_FORBIDDEN,
        )

        # Admin mode lets the pod admin delete the row; it is then gone for everyone.
        await pod_api.delete_record("expenses", editor_row["id"], mode="ADMIN")
        remaining = await editor_api.list_records("expenses", limit=20)
        assert remaining["items"] == []

    @pytest.mark.asyncio
    async def test_sql_query_joins_rls_and_shared_tables_scoped_per_user(
        self,
        pod_api: DatastoreApi,
        authenticated_client: AsyncClient,
        async_client: AsyncClient,
        fixed_test_org,
        member_users,
    ):
        """A join across an RLS table and a shared table returns only the caller's RLS rows."""
        await pod_api.create_table(
            {
                "name": "categories",
                "enable_rls": False,
                "columns": [{"name": "name", "type": "TEXT", "required": True}],
            }
        )
        await pod_api.create_table(
            {
                "name": "expenses",
                "enable_rls": True,
                "columns": [
                    {"name": "merchant", "type": "TEXT", "required": True},
                    {"name": "amount", "type": "FLOAT", "required": True},
                    {"name": "category", "type": "TEXT", "required": True},
                ],
            }
        )
        # A shared category row is visible to every member (non-RLS table).
        await pod_api.create_record("categories", {"name": "Meals"})

        second_editor = await signup_user(async_client, "datastore-join-editor-two")
        await invite_to_pod(
            authenticated_client,
            async_client,
            org_id=fixed_test_org["id"],
            pod_id=pod_api.pod_id,
            user=second_editor,
            role="POD_EDITOR",
        )
        editor_api = DatastoreApi(async_client, pod_api.pod_id, member_users["editor"])
        second_editor_api = DatastoreApi(async_client, pod_api.pod_id, second_editor)

        await editor_api.create_record(
            "expenses",
            {"merchant": "Editor Lunch", "amount": 12.5, "category": "Meals"},
        )
        await second_editor_api.create_record(
            "expenses",
            {"merchant": "Second Editor Taxi", "amount": 31.0, "category": "Meals"},
        )

        join_sql = (
            "SELECT e.merchant, c.name AS category "
            "FROM expenses e JOIN categories c ON e.category = c.name "
            "ORDER BY e.merchant ASC"
        )

        # Each member sees only their own expense joined to the shared category.
        editor_join = await editor_api.query(join_sql)
        assert editor_join["items"] == [
            {"merchant": "Editor Lunch", "category": "Meals"}
        ]
        second_join = await second_editor_api.query(join_sql)
        assert second_join["items"] == [
            {"merchant": "Second Editor Taxi", "category": "Meals"}
        ]

        # By default the pod admin is scoped to their own (zero) RLS rows, so the
        # join is empty; mode=admin widens it to every member's expense.
        admin_default_join = await pod_api.query(join_sql)
        assert admin_default_join["items"] == []

        admin_join = await pod_api.query(join_sql, mode="ADMIN")
        assert [row["merchant"] for row in admin_join["items"]] == [
            "Editor Lunch",
            "Second Editor Taxi",
        ]

    @pytest.mark.asyncio
    async def test_sql_query_rejects_cross_schema_and_mutations(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        """Cross-schema/catalog references, stacked statements, and mutations are rejected."""
        pod_api = project_workspace

        catalog = await pod_api.query(
            "SELECT * FROM pg_catalog.pg_user",
            expected_status=status.HTTP_400_BAD_REQUEST,
        )
        assert catalog["code"] == "DATASTORE_QUERY_ERROR"

        stacked = await pod_api.query(
            "SELECT 1; DROP TABLE projects",
            expected_status=status.HTTP_400_BAD_REQUEST,
        )
        assert stacked["code"] == "DATASTORE_QUERY_ERROR"

        update = await pod_api.query(
            "UPDATE projects SET name = 'x'",
            expected_status=status.HTTP_400_BAD_REQUEST,
        )
        assert update["code"] == "DATASTORE_QUERY_ERROR"

    @pytest.mark.asyncio
    async def test_sql_query_enforces_row_cap_and_cost_guard(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
        monkeypatch,
    ):
        """The row cap truncates large results; the EXPLAIN cost guard rejects pathological queries."""
        from app.modules.datastore.config import datastore_settings

        pod_api = project_workspace
        await _seed_projects(pod_api, fixed_test_user["id"])  # seeds two projects

        # Row cap: shrink it and confirm the result is truncated to the cap.
        monkeypatch.setattr(datastore_settings, "datastore_query_max_rows", 1)
        capped = await pod_api.query("SELECT id FROM projects")
        assert capped["total"] == 1
        assert len(capped["items"]) == 1

        # Cost guard: force a tiny ceiling and confirm a normal query is rejected
        # before execution.
        monkeypatch.setattr(datastore_settings, "datastore_query_max_cost", 0.0)
        rejected = await pod_api.query(
            "SELECT * FROM projects",
            expected_status=status.HTTP_400_BAD_REQUEST,
        )
        assert rejected["code"] == "DATASTORE_QUERY_ERROR"


class TestDatastoreRecordErrorMessages:
    """E2E tests for clean, agent-readable error messages on record write failures."""

    @pytest.mark.asyncio
    async def test_enum_violation_returns_allowed_values_in_details(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        """Creating a record with an invalid ENUM value returns a 400 with
        ``details.allowed_values`` so the agent can self-correct."""
        resp = await project_workspace.request(
            "POST",
            f"/pods/{project_workspace.pod_id}/datastore/tables/projects/records",
            json={"data": {"name": "Bad Status", "status": "draft"}},
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.text
        body = resp.json()
        assert body["code"] == "DATASTORE_VALIDATION_ERROR"
        assert "draft" in body["message"]
        assert "allowed" in body["message"].lower()
        details = body.get("details")
        assert details is not None
        assert "errors" in details
        enum_err = next(e for e in details["errors"] if e.get("reason") == "enum")
        assert enum_err["field"] == "status"
        assert set(enum_err["allowed_values"]) == {"planned", "active", "done"}

    @pytest.mark.asyncio
    async def test_enum_violation_on_update_returns_allowed_values(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        """Updating a record with an invalid ENUM value also returns allowed values."""
        rows = await _seed_projects(project_workspace, fixed_test_user["id"])
        apollo = rows["apollo"]
        resp = await project_workspace.request(
            "PATCH",
            f"/pods/{project_workspace.pod_id}/datastore/tables/projects/records/{apollo['id']}",
            json={"data": {"status": "archived"}},
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        body = resp.json()
        assert body["code"] == "DATASTORE_VALIDATION_ERROR"
        assert "archived" in body["message"]
        details = body.get("details")
        assert details is not None
        enum_err = next(e for e in details["errors"] if e.get("reason") == "enum")
        assert enum_err["field"] == "status"

    @pytest.mark.asyncio
    async def test_foreign_key_violation_returns_clean_message(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        """Creating a milestone with a non-existent project_id returns a clean 400
        — no raw SQL or parameter leak."""
        resp = await project_workspace.request(
            "POST",
            f"/pods/{project_workspace.pod_id}/datastore/tables/milestones/records",
            json={"data": {"project_id": str(uuid4()), "title": "Orphan"}},
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.text
        body = resp.json()
        assert body["code"] == "DATASTORE_VALIDATION_ERROR"
        assert "project_id" in body["message"]
        assert "non-existent" in body["message"].lower()
        assert "INSERT" not in body["message"]
        assert "SQL" not in body["message"]

    @pytest.mark.asyncio
    async def test_not_null_violation_returns_clean_message(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        """Creating a record missing a required column returns a clean 400."""
        resp = await project_workspace.request(
            "POST",
            f"/pods/{project_workspace.pod_id}/datastore/tables/projects/records",
            json={"data": {"status": "active"}},
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.text
        body = resp.json()
        assert body["code"] == "DATASTORE_VALIDATION_ERROR"
        assert "name" in body["message"]
        assert "required" in body["message"].lower()

    @pytest.mark.asyncio
    async def test_bad_filter_operator_returns_allowed_operators(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        """An unsupported filter operator returns 400 with ``details.allowed_operators``."""
        await _seed_projects(project_workspace, fixed_test_user["id"])
        resp = await project_workspace.request(
            "GET",
            f"/pods/{project_workspace.pod_id}/datastore/tables/projects/records",
            params={"filter": '{"field":"name","op":"contains","value":"Apollo"}'},
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        body = resp.json()
        assert body["code"] == "DATASTORE_VALIDATION_ERROR"
        assert "contains" in body["message"]
        details = body.get("details")
        assert details is not None
        assert "eq" in details["allowed_operators"]
        assert "like" in details["allowed_operators"]
        assert "in" in details["allowed_operators"]

    @pytest.mark.asyncio
    async def test_unknown_column_query_returns_clean_message_not_driver_class(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        """An unknown column must not leak ``<class 'asyncpg…'>`` to the caller."""
        await _seed_projects(project_workspace, fixed_test_user["id"])
        body = await project_workspace.query(
            "SELECT no_such_column FROM projects",
            expected_status=status.HTTP_400_BAD_REQUEST,
        )
        message = body["message"]
        assert "no_such_column" in message
        assert "does not exist" in message
        assert "asyncpg" not in message
        assert "<class" not in message


class TestRecordRequestsAreBounded:
    """`PS-DATA-011` and `PS-DATA-013` promise ceilings; these are them.

    Both are refusals with an exact shape, which is why they live here rather
    than in a scenario: the point is the status code and that the message names
    the limit, not the happy path.
    """

    @pytest.mark.asyncio
    async def test_a_page_larger_than_the_cap_is_refused(
        self,
        project_workspace: DatastoreApi,
    ):
        """`PS-DATA-011`: a table with many records cannot be pulled in one go.

        `record.list` is also the one datastore read the EXPLAIN cost ceiling
        does not cover, so an unbounded ``limit`` had nothing above it at all.
        """
        response = await project_workspace.request(
            "GET",
            f"/pods/{project_workspace.pod_id}/datastore/tables/projects/records",
            params={"limit": MAX_RECORD_PAGE_SIZE + 1},
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, (
            f"an unbounded page size was accepted: {response.text[:300]}"
        )

    @pytest.mark.asyncio
    async def test_the_largest_allowed_page_is_still_allowed(
        self,
        project_workspace: DatastoreApi,
    ):
        listed = await project_workspace.list_records(
            "projects", limit=MAX_RECORD_PAGE_SIZE
        )

        assert listed["limit"] == MAX_RECORD_PAGE_SIZE

    @pytest.mark.asyncio
    async def test_a_bulk_batch_larger_than_the_cap_is_refused_and_says_the_limit(
        self,
        project_workspace: DatastoreApi,
    ):
        """`PS-DATA-013`: say the limit rather than truncating silently."""
        oversized = [
            {"name": f"row {index}", "status": "planned"}
            for index in range(MAX_BULK_RECORDS + 1)
        ]

        response = await project_workspace.request(
            "POST",
            f"/pods/{project_workspace.pod_id}"
            "/datastore/tables/projects/records/bulk/create",
            json={"records": oversized},
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, (
            f"an unbounded batch was accepted: {response.text[:300]}"
        )
        assert str(MAX_BULK_RECORDS) in response.text, (
            f"the refusal does not say what the limit is: {response.text[:400]}"
        )


class TestBulkDeleteIsAllOrNothing:
    """`PS-DATA-013` for the one destructive member of the family.

    ``bulk_create`` and ``bulk_update`` were both rewritten into a single
    transaction; delete was still a loop of per-row commits, so a batch that
    failed halfway left the first half destroyed with no way to learn which.
    """

    @pytest.mark.asyncio
    async def test_an_unknown_id_deletes_nothing_and_is_named(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        pod_api = project_workspace
        rows = await _seed_projects(pod_api, fixed_test_user["id"])
        missing_id = str(uuid4())

        response = await pod_api.request(
            "POST",
            f"/pods/{pod_api.pod_id}/datastore/tables/projects/records/bulk/delete",
            json={"record_ids": [rows["apollo"]["id"], missing_id]},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND, (
            f"an id that matched nothing was dropped from the count instead of "
            f"reported: {response.status_code} {response.text[:300]}"
        )
        assert missing_id in response.text, (
            f"the refusal does not say which id failed: {response.text[:400]}"
        )
        survivors = await pod_api.list_records("projects")
        assert rows["apollo"]["id"] in {row["id"] for row in survivors["items"]}, (
            "the batch was refused, so the row that did match must still be there"
        )

    @pytest.mark.asyncio
    async def test_a_batch_that_all_matches_still_deletes_every_row(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        pod_api = project_workspace
        rows = await _seed_projects(pod_api, fixed_test_user["id"])

        deleted = await pod_api.bulk_delete(
            "projects", [rows["apollo"]["id"], rows["beacon"]["id"]]
        )

        assert deleted["count"] == 2
        assert (await pod_api.list_records("projects"))["items"] == []


class TestQueryResultsSayWhenTheyWereCut:
    @pytest.mark.asyncio
    async def test_a_capped_result_is_not_reported_as_a_complete_one(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
        monkeypatch,
    ):
        """A row-capped answer that says nothing reads as the whole table.

        The repository computes the flag precisely so a caller cannot mistake
        one for the other -- an agent otherwise tells someone with forty
        thousand orders that they have a thousand. The HTTP API dropped it.
        """
        from app.modules.datastore.config import datastore_settings

        pod_api = project_workspace
        await _seed_projects(pod_api, fixed_test_user["id"])  # seeds two projects
        monkeypatch.setattr(datastore_settings, "datastore_query_max_rows", 1)

        capped = await pod_api.query("SELECT id FROM projects")

        assert len(capped["items"]) == 1
        assert capped["truncated"] is True, (
            f"a result cut by the row cap claims to be complete: {capped}"
        )

    @pytest.mark.asyncio
    async def test_a_complete_result_says_so(
        self,
        project_workspace: DatastoreApi,
        fixed_test_user,
    ):
        pod_api = project_workspace
        await _seed_projects(pod_api, fixed_test_user["id"])

        result = await pod_api.query("SELECT id FROM projects")

        assert result["total"] == 2
        assert result["truncated"] is False


class TestPagingOverANonUniqueSortIsStable:
    """`PS-DATA-011`: paging an unchanging table returns every record once.

    The default sort already appends the primary key for exactly this reason,
    with the reason written next to it. An explicit ``sort`` did not get it, so
    ordering by a status or a date left the order among equal rows to the
    planner -- and offset paging over an unspecified order can serve one row
    twice and never serve another.
    """

    @pytest.mark.asyncio
    async def test_every_row_is_seen_exactly_once_when_the_sort_column_ties(
        self,
        project_workspace: DatastoreApi,
    ):
        pod_api = project_workspace
        # Every row shares one status, so the sort column decides nothing.
        await pod_api.bulk_create(
            "projects",
            [{"name": f"project {index}", "status": "active"} for index in range(30)],
        )

        seen: list[str] = []
        for offset in range(0, 30, 5):
            page = await pod_api.list_records(
                "projects",
                limit=5,
                offset=offset,
                sort='{"field":"status","direction":"asc"}',
            )
            seen.extend(row["id"] for row in page["items"])

        assert len(seen) == 30
        assert len(set(seen)) == 30, (
            "paging over a sort column whose values all tie repeated a row and "
            "dropped another; the page boundary has to be total"
        )
