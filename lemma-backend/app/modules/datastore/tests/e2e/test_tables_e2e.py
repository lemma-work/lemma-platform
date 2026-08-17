"""E2E tests for datastore table lifecycle: schema, columns, visibility rules.

Covers table creation/validation, the system-managed-column contract, schema
introspection, config updates, and add/remove-column behaviour. Record and
query behaviour live in ``test_records_e2e.py``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import status

from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e


class TestDatastoreTableLifecycle:
    @pytest.mark.asyncio
    async def test_create_table_rejects_duplicate_name_in_same_pod(
        self,
        pod_api: DatastoreApi,
    ):
        """A second table created with an existing name in the pod is a 409 conflict."""
        table_name = f"duplicate_table_{uuid4().hex[:8]}"
        payload = {
            "name": table_name,
            "enable_rls": True,
            "columns": [{"name": "title", "type": "TEXT"}],
        }

        await pod_api.create_table(payload)
        duplicate = await pod_api.create_table(
            payload,
            expected_status=status.HTTP_409_CONFLICT,
        )
        assert duplicate["code"] == "DATASTORE_CONFLICT"

    @pytest.mark.asyncio
    async def test_creating_table_with_system_managed_column_is_rejected(
        self,
        pod_api: DatastoreApi,
    ):
        """Declaring a system-managed column (e.g. created_at) on a new table is a 400."""
        invalid_table = await pod_api.request(
            "POST",
            f"/pods/{pod_api.pod_id}/datastore/tables",
            json={
                "name": "invalid_timestamps",
                "enable_rls": False,
                "columns": [
                    {"name": "title", "type": "TEXT", "required": True},
                    {"name": "created_at", "type": "DATETIME"},
                ],
            },
        )
        assert invalid_table.status_code == status.HTTP_400_BAD_REQUEST
        assert "System-managed columns" in invalid_table.text

    @pytest.mark.asyncio
    async def test_tables_can_be_created_and_listed_with_pagination(
        self,
        pod_api: DatastoreApi,
    ):
        """Creating several rich-schema tables surfaces them via paginated list_tables."""
        projects = await pod_api.create_table(
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
        assert projects["name"] == "projects"

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

        page_one = await pod_api.list_tables(limit=2)
        assert page_one["next_page_token"]
        page_two = await pod_api.list_tables(
            limit=10, page_token=page_one["next_page_token"]
        )
        assert {"projects", "milestones", "snapshots"} <= {
            item["name"] for item in page_one["items"] + page_two["items"]
        }

    @pytest.mark.asyncio
    async def test_table_schema_reports_system_and_typed_columns(
        self,
        pod_api: DatastoreApi,
    ):
        """get_table exposes auto/system flags and USER/FILE_PATH column types."""
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

        project_schema = await pod_api.get_table("projects")
        columns = {column["name"]: column for column in project_schema["columns"]}
        assert columns["id"]["auto"] is True
        assert columns["created_at"]["system"] is True
        assert columns["updated_at"]["system"] is True
        assert columns["owner_user"]["type"] == "USER"
        assert columns["artifact_path"]["type"] == "FILE_PATH"
        # An ENUM without its options is unwritable: the caller has to guess
        # which values the check constraint will accept. This table already had
        # an ENUM column and nothing asserted the options came back, which is
        # how `tables get` came to be reported as never showing them.
        assert columns["status"]["options"] == ["planned", "active", "done"]

    @pytest.mark.asyncio
    async def test_table_config_and_columns_can_be_updated_but_system_columns_are_protected(
        self,
        pod_api: DatastoreApi,
    ):
        """Config/computed/plain columns are editable; removing a system column is a 403."""
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
                ],
                "config": {"label": "Projects"},
            }
        )

        updated_table = await pod_api.update_table(
            "projects",
            {"config": {"label": "Projects", "view": "board"}},
        )
        assert updated_table["config"]["view"] == "board"
        await pod_api.add_column(
            "projects",
            {
                "name": "project_summary",
                "type": "TEXT",
                "computed": True,
                "expression": "name || ' [' || status || ']'",
            },
        )
        await pod_api.add_column("projects", {"name": "risk_level", "type": "TEXT"})
        await pod_api.remove_column("projects", "risk_level")
        await pod_api.remove_column(
            "projects",
            "created_at",
            expected_status=status.HTTP_403_FORBIDDEN,
        )


class TestReservedTablesHidden:
    @pytest.mark.asyncio
    async def test_reserved_tables_are_hidden_from_list_and_get(
        self,
        pod_api: DatastoreApi,
        db_manager,
    ):
        """System-managed ``reserved_*`` tables are never surfaced by the API:
        filtered out of the table list and 404 on get-by-name, even when the table
        is registered in the pod's metadata."""
        from uuid import UUID

        from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
        from app.core.infrastructure.events.message_bus import get_message_bus
        from app.modules.datastore.domain.datastore_entities import (
            ColumnSchema,
            DatastoreDataType,
            DatastoreTableEntity,
        )
        from app.modules.datastore.infrastructure.repositories import (
            DatastoreTableRepository,
        )

        pod_uuid = UUID(str(pod_api.pod_id))

        # A normal table stays visible.
        visible_name = f"visible_{uuid4().hex[:8]}"
        await pod_api.create_table(
            {
                "name": visible_name,
                "enable_rls": False,
                "columns": [{"name": "title", "type": "TEXT"}],
            }
        )

        # Register a reserved_users table directly — the API refuses to create one,
        # so we go through the repository, mirroring the old member-sync path.
        uow_factory = SessionUnitOfWorkFactory(db_manager.session_factory)
        async with uow_factory() as uow:
            repo = DatastoreTableRepository(uow, message_bus=get_message_bus())
            await repo.create(
                DatastoreTableEntity(
                    pod_id=pod_uuid,
                    table_name="reserved_users",
                    columns=[
                        ColumnSchema(
                            name="user_id", type=DatastoreDataType.TEXT, required=True
                        ),
                        ColumnSchema(name="email", type=DatastoreDataType.TEXT),
                    ],
                    primary_key_column="user_id",
                    enable_rls=False,
                )
            )

        listing = await pod_api.list_tables(limit=1000)
        names = {item["name"] for item in listing["items"]}
        assert visible_name in names
        assert "reserved_users" not in names

        # Get-by-name is a 404 even though the metadata row exists.
        await pod_api.get_table(
            "reserved_users", expected_status=status.HTTP_404_NOT_FOUND
        )
