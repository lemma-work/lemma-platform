"""E2E tests for datastore authorization boundaries.

Covers resource grants (table records + folder writes), table/file list
visibility under pod and custom roles, pod-role expectations for tables and
files, and per-user RLS-record mutation isolation.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import status
from httpx import AsyncClient

from app.modules.datastore.tests.e2e.harness import (
    DatastoreApi,
    index_file,
    invite_to_pod,
    signup_user,
)
from app.modules.test_support.e2e_authz import create_role_visibility_context

pytestmark = pytest.mark.e2e

# pod-member-sync asynchronously creates this internal table the first time a pod
# has members. It is not the subject of these role-visibility checks and its
# creation races the assertions under parallel load, so exclude it from table
# name comparisons.
_RESERVED_USERS_TABLE = "reserved_users"


def _table_names(listing: dict) -> set[str]:
    """Table names in a list response, minus the internal reserved_users table."""
    return {item["name"] for item in listing["items"]} - {_RESERVED_USERS_TABLE}


class TestDatastoreAuthorizationBoundaries:
    @pytest.mark.asyncio
    async def test_resource_grants_allow_restricted_table_records_and_folder_writes(
        self,
        authenticated_client: AsyncClient,
        async_client: AsyncClient,
        fixed_test_org,
        index_datastore_file,
    ):
        """A custom role is denied a RESTRICTED table/folder until a resource grant opens record writes and folder writes."""
        ctx = await create_role_visibility_context(
            authenticated_client,
            async_client,
            fixed_test_org,
            pod_name_prefix="datastore-resource-grants",
            custom_role="DATA_OPERATORS",
        )
        pod_id = ctx["pod_id"]
        owner_api = DatastoreApi(authenticated_client, pod_id)
        operator_api = DatastoreApi(async_client, pod_id, ctx["custom_viewer"])
        suffix = uuid4().hex[:8]
        table_name = f"restricted_expenses_{suffix}"
        folder_path = f"/restricted-folder-{suffix}"

        table = await owner_api.create_table(
            {
                "name": table_name,
                "visibility": "RESTRICTED",
                "enable_rls": True,
                "columns": [
                    {"name": "merchant", "type": "TEXT", "required": True},
                    {"name": "amount", "type": "FLOAT"},
                ],
            }
        )
        folder = await owner_api.create_folder(
            folder_path,
            visibility="RESTRICTED",
        )
        restricted_file = await owner_api.upload_file(
            "grant-visible.md",
            b"grant only datastore search marker",
            directory_path=folder_path,
        )
        await index_file(index_datastore_file, restricted_file)

        await operator_api.get_table(
            table_name,
            expected_status=status.HTTP_403_FORBIDDEN,
        )
        await operator_api.get_file(
            restricted_file["path"],
            expected_status=status.HTTP_403_FORBIDDEN,
        )
        hidden_root_files = await operator_api.list_files(directory_path="/")
        assert folder["id"] not in {
            item["id"] for item in hidden_root_files["items"]
        }
        hidden_search = await operator_api.search_files(
            "grant only datastore search marker"
        )
        assert restricted_file["id"] not in {
            item["file_id"] for item in hidden_search["items"]
        }
        await operator_api.create_record(
            table_name,
            {"merchant": "Denied", "amount": 1},
            expected_status=status.HTTP_403_FORBIDDEN,
        )
        await operator_api.upload_file(
            "denied.md",
            b"denied",
            directory_path=folder_path,
            expected_status=status.HTTP_403_FORBIDDEN,
        )

        grant_response = await authenticated_client.put(
            f"/pods/{pod_id}/roles/{ctx['custom_role']}/permissions",
            json={
                "grants": [
                    {
                        "resource_type": "datastore_table",
                        "resource_name": table["name"],
                        "permission_ids": [
                            "datastore.table.read",
                            "datastore.record.write",
                        ],
                    },
                    {
                        "resource_type": "folder",
                        "resource_name": folder["path"],
                        "permission_ids": ["folder.read", "folder.write"],
                    },
                ]
            },
        )
        assert grant_response.status_code == status.HTTP_200_OK, grant_response.text

        granted_table = await operator_api.get_table(table_name)
        assert granted_table["id"] == table["id"]
        granted_file = await operator_api.get_file(restricted_file["path"])
        assert granted_file["id"] == restricted_file["id"]
        visible_root_files = await operator_api.list_files(directory_path="/")
        assert folder["id"] in {item["id"] for item in visible_root_files["items"]}
        visible_files = await operator_api.list_files(directory_path=folder_path)
        assert restricted_file["id"] in {item["id"] for item in visible_files["items"]}
        visible_search = await operator_api.search_files(
            "grant only datastore search marker"
        )
        assert restricted_file["id"] in {
            item["file_id"] for item in visible_search["items"]
        }
        record = await operator_api.create_record(
            table_name,
            {"merchant": "Taxi", "amount": 42},
        )
        assert record["merchant"] == "Taxi"
        uploaded = await operator_api.upload_file(
            "granted.md",
            b"granted folder write",
            directory_path=folder_path,
        )
        assert uploaded["path"] == f"{folder_path}/granted.md"
        assert await operator_api.download_file(uploaded["path"]) == (
            b"granted folder write"
        )

    @pytest.mark.asyncio
    async def test_table_list_visibility_respects_pod_and_custom_roles(
        self,
        authenticated_client: AsyncClient,
        async_client: AsyncClient,
        fixed_test_org,
    ):
        """Table lists and allowed_actions reflect default visibility plus per-role RESTRICTED grants."""
        ctx = await create_role_visibility_context(
            authenticated_client,
            async_client,
            fixed_test_org,
            pod_name_prefix="datastore-visibility",
            custom_role="DATA_REVIEWERS",
        )
        pod_id = ctx["pod_id"]
        owner_api = DatastoreApi(authenticated_client, pod_id)
        viewer_api = DatastoreApi(async_client, pod_id, ctx["viewer"])
        editor_api = DatastoreApi(async_client, pod_id, ctx["editor"])
        custom_api = DatastoreApi(async_client, pod_id, ctx["custom_viewer"])

        table_payload = {
            "enable_rls": False,
            "columns": [{"name": "title", "type": "TEXT"}],
        }
        table_names = {
            "default": f"default_table_{uuid4().hex[:8]}",
            "editor": f"editor_table_{uuid4().hex[:8]}",
            "custom": f"custom_table_{uuid4().hex[:8]}",
        }
        await owner_api.create_table({"name": table_names["default"], **table_payload})
        editor_table = await owner_api.create_table(
            {
                "name": table_names["editor"],
                "visibility": "RESTRICTED",
                **table_payload,
            }
        )
        custom_table = await owner_api.create_table(
            {
                "name": table_names["custom"],
                "visibility": "RESTRICTED",
                **table_payload,
            }
        )

        editor_table_grant = await authenticated_client.put(
            f"/pods/{pod_id}/roles/POD_EDITOR/permissions",
            json={
                "grants": [
                    {
                        "resource_type": "datastore_table",
                        "resource_name": editor_table["name"],
                        "permission_ids": [
                            "datastore.table.read",
                            "datastore.table.update",
                        ],
                    }
                ]
            },
        )
        assert editor_table_grant.status_code == status.HTTP_200_OK, (
            editor_table_grant.text
        )
        custom_table_grant = await authenticated_client.put(
            f"/pods/{pod_id}/roles/{ctx['custom_role']}/permissions",
            json={
                "grants": [
                    {
                        "resource_type": "datastore_table",
                        "resource_name": custom_table["name"],
                        "permission_ids": ["datastore.table.read"],
                    }
                ]
            },
        )
        assert custom_table_grant.status_code == status.HTTP_200_OK, (
            custom_table_grant.text
        )

        viewer_tables = await viewer_api.list_tables(limit=20)
        assert _table_names(viewer_tables) == {table_names["default"]}

        editor_tables = await editor_api.list_tables(limit=20)
        assert _table_names(editor_tables) == {
            table_names["default"],
            table_names["editor"],
        }
        editor_table_items = {item["name"]: item for item in editor_tables["items"]}
        assert set(editor_table_items[table_names["default"]]["allowed_actions"]) == {
            "datastore.table.read",
            "datastore.record.read",
            "datastore.record.write",
            "datastore.table.update",
        }
        assert set(editor_table_items[table_names["editor"]]["allowed_actions"]) == {
            "datastore.table.read",
            "datastore.table.update",
        }
        editor_get_default_table = await editor_api.get_table(table_names["default"])
        assert set(editor_get_default_table["allowed_actions"]) == {
            "datastore.table.read",
            "datastore.record.read",
            "datastore.record.write",
            "datastore.table.update",
        }
        editor_get_restricted_table = await editor_api.get_table(table_names["editor"])
        assert set(editor_get_restricted_table["allowed_actions"]) == {
            "datastore.table.read",
            "datastore.table.update",
        }

        custom_tables = await custom_api.list_tables(limit=20)
        assert _table_names(custom_tables) == {
            table_names["default"],
            table_names["custom"],
        }
        custom_table_items = {item["name"]: item for item in custom_tables["items"]}
        assert set(custom_table_items[table_names["default"]]["allowed_actions"]) == {
            "datastore.record.read",
            "datastore.table.read"
        }
        assert set(custom_table_items[table_names["custom"]]["allowed_actions"]) == {
            "datastore.table.read"
        }
        custom_get_restricted_table = await custom_api.get_table(table_names["custom"])
        assert set(custom_get_restricted_table["allowed_actions"]) == {
            "datastore.table.read"
        }

        await viewer_api.get_table(
            table_names["editor"],
            expected_status=status.HTTP_403_FORBIDDEN,
        )
        await custom_api.update_table(
            table_names["custom"],
            {"config": {"label": "custom viewer edit"}},
            expected_status=status.HTTP_403_FORBIDDEN,
        )
        await editor_api.update_table(
            table_names["editor"],
            {"config": {"label": "editor edit"}},
        )

    @pytest.mark.asyncio
    async def test_file_list_and_search_visibility_respects_pod_and_custom_roles(
        self,
        authenticated_client: AsyncClient,
        async_client: AsyncClient,
        fixed_test_org,
        index_datastore_file,
    ):
        """File lists, get_file allowed_actions, and search reflect default visibility plus per-role document grants."""
        ctx = await create_role_visibility_context(
            authenticated_client,
            async_client,
            fixed_test_org,
            pod_name_prefix="datastore-visibility",
            custom_role="DATA_REVIEWERS",
        )
        pod_id = ctx["pod_id"]
        owner_api = DatastoreApi(authenticated_client, pod_id)
        viewer_api = DatastoreApi(async_client, pod_id, ctx["viewer"])
        editor_api = DatastoreApi(async_client, pod_id, ctx["editor"])
        custom_api = DatastoreApi(async_client, pod_id, ctx["custom_viewer"])

        await owner_api.create_folder("/shared")
        default_file = await owner_api.upload_file(
            "default.md",
            b"default shared editor visibility fallback",
            directory_path="/shared",
        )
        editor_file = await owner_api.upload_file(
            "editor.md",
            b"editor shared",
            directory_path="/shared",
            visibility="RESTRICTED",
        )
        custom_file = await owner_api.upload_file(
            "custom.md",
            b"custom shared",
            directory_path="/shared",
            visibility="RESTRICTED",
        )

        editor_file_grant = await authenticated_client.put(
            f"/pods/{pod_id}/roles/POD_EDITOR/permissions",
            json={
                "grants": [
                    {
                        "resource_type": "document",
                        "resource_name": editor_file["path"],
                        "permission_ids": ["folder.read"],
                    },
                ]
            },
        )
        assert editor_file_grant.status_code == status.HTTP_200_OK, (
            editor_file_grant.text
        )
        custom_file_grant = await authenticated_client.put(
            f"/pods/{pod_id}/roles/{ctx['custom_role']}/permissions",
            json={
                "grants": [
                    {
                        "resource_type": "document",
                        "resource_name": custom_file["path"],
                        "permission_ids": ["folder.read"],
                    },
                ]
            },
        )
        assert custom_file_grant.status_code == status.HTTP_200_OK, (
            custom_file_grant.text
        )

        viewer_files = await viewer_api.list_files(
            directory_path="/shared",
        )
        assert {item["id"] for item in viewer_files["items"]} == {default_file["id"]}

        editor_files = await editor_api.list_files(
            directory_path="/shared",
        )
        assert {item["id"] for item in editor_files["items"]} == {
            default_file["id"],
            editor_file["id"],
        }
        editor_file_items = {item["id"]: item for item in editor_files["items"]}
        assert set(editor_file_items[default_file["id"]]["allowed_actions"]) == {
            "folder.read",
            "folder.write",
        }
        assert set(editor_file_items[editor_file["id"]]["allowed_actions"]) == {
            "folder.read"
        }
        editor_get_default_file = await editor_api.get_file(default_file["path"])
        assert set(editor_get_default_file["allowed_actions"]) == {
            "folder.read",
            "folder.write",
        }
        editor_get_restricted_file = await editor_api.get_file(editor_file["path"])
        assert set(editor_get_restricted_file["allowed_actions"]) == {
            "folder.read"
        }

        custom_files = await custom_api.list_files(
            directory_path="/shared",
        )
        assert {item["id"] for item in custom_files["items"]} == {
            default_file["id"],
            custom_file["id"],
        }
        custom_file_items = {item["id"]: item for item in custom_files["items"]}
        assert set(custom_file_items[default_file["id"]]["allowed_actions"]) == {
            "folder.read"
        }
        assert set(custom_file_items[custom_file["id"]]["allowed_actions"]) == {
            "folder.read"
        }
        custom_get_restricted_file = await custom_api.get_file(custom_file["path"])
        assert set(custom_get_restricted_file["allowed_actions"]) == {
            "folder.read"
        }

        for file_entity in (default_file, editor_file, custom_file):
            await index_file(index_datastore_file, file_entity)

        for method in ("TEXT", "VECTOR", "HYBRID"):
            viewer_search = await viewer_api.search_files(
                "editor",
                search_method=method,
                limit=1,
            )
            assert [item["file_id"] for item in viewer_search["items"]] == [
                default_file["id"]
            ]

    @pytest.mark.asyncio
    async def test_pod_roles_match_user_expectations_for_tables_and_files(
        self,
        pod_api: DatastoreApi,
        async_client: AsyncClient,
        member_users,
    ):
        """POD_VIEWER cannot create/edit tables or files, while POD_EDITOR can, and both see shared content."""
        viewer_api = DatastoreApi(async_client, pod_api.pod_id, member_users["viewer"])
        editor_api = DatastoreApi(async_client, pod_api.pod_id, member_users["editor"])

        viewer_create_table = await viewer_api.request(
            "POST",
            f"/pods/{pod_api.pod_id}/datastore/tables",
            json={
                "name": "viewer_table",
                "enable_rls": False,
                "columns": [{"name": "title", "type": "TEXT"}],
            },
        )
        assert viewer_create_table.status_code == status.HTTP_403_FORBIDDEN

        await editor_api.create_table(
            {
                "name": "editor_table",
                "enable_rls": False,
                "columns": [{"name": "title", "type": "TEXT"}],
            }
        )
        viewer_tables = await viewer_api.list_tables(limit=20)
        assert "editor_table" in {item["name"] for item in viewer_tables["items"]}

        await editor_api.delete_table("editor_table")

        await pod_api.create_folder("/shared")
        pod_file = await pod_api.upload_file(
            "guide.md",
            b"shared guide",
            directory_path="/shared",
        )
        assert await viewer_api.download_file(pod_file["path"]) == b"shared guide"
        await viewer_api.update_file(
            pod_file["path"],
            content=b"viewer edit",
            expected_status=status.HTTP_403_FORBIDDEN,
        )
        await editor_api.update_file(
            pod_file["path"],
            content=b"editor edit",
        )
        assert await viewer_api.download_file(pod_file["path"]) == b"editor edit"

    @pytest.mark.asyncio
    async def test_pod_user_is_row_scoped_in_rls_tables_and_can_write_shared_tables(
        self,
        pod_api: DatastoreApi,
        authenticated_client: AsyncClient,
        async_client: AsyncClient,
        fixed_test_org,
        member_users,
    ):
        """A POD_USER holds DATASTORE_RECORD_WRITE pod-wide: in an RLS table they
        are scoped to their own rows (other users' rows are invisible -> 404),
        and they may also write shared (non-RLS) tables. RLS governs only
        row-level scoping, not which permission a write requires."""
        pod_user = await signup_user(async_client, "datastore-pod-user")
        await invite_to_pod(
            authenticated_client,
            async_client,
            org_id=fixed_test_org["id"],
            pod_id=pod_api.pod_id,
            user=pod_user,
            role="POD_USER",
        )
        pod_user_api = DatastoreApi(async_client, pod_api.pod_id, pod_user)
        editor_api = DatastoreApi(async_client, pod_api.pod_id, member_users["editor"])

        await pod_api.create_table(
            {
                "name": "owned_notes",
                "enable_rls": True,
                "columns": [
                    {"name": "title", "type": "TEXT", "required": True},
                    {"name": "status", "type": "TEXT"},
                ],
            }
        )
        await pod_api.create_table(
            {
                "name": "shared_notes",
                "enable_rls": False,
                "columns": [{"name": "title", "type": "TEXT", "required": True}],
            }
        )

        user_row = await pod_user_api.create_record(
            "owned_notes",
            {"title": "Mine", "status": "draft"},
        )
        editor_row = await editor_api.create_record(
            "owned_notes",
            {"title": "Editor", "status": "draft"},
        )

        assert user_row["user_id"] == pod_user["id"]
        assert editor_row["user_id"] == member_users["editor"]["id"]

        updated = await pod_user_api.update_record(
            "owned_notes",
            user_row["id"],
            {"status": "done"},
        )
        assert updated["status"] == "done"

        update_other = await pod_user_api.request(
            "PATCH",
            f"/pods/{pod_api.pod_id}/datastore/tables/owned_notes/records/{editor_row['id']}",
            json={"data": {"status": "stolen"}},
        )
        assert update_other.status_code == status.HTTP_404_NOT_FOUND

        # A POD_USER holds DATASTORE_RECORD_WRITE pod-wide, so they may also
        # write shared (non-RLS) tables. RLS only scopes which ROWS a non-admin
        # can touch; it does not change which permission a write requires.
        shared_row = await pod_user_api.create_record(
            "shared_notes",
            {"title": "shared row"},
        )
        assert shared_row["title"] == "shared row"

        await pod_user_api.delete_record("owned_notes", user_row["id"])
        delete_other = await pod_user_api.request(
            "DELETE",
            f"/pods/{pod_api.pod_id}/datastore/tables/owned_notes/records/{editor_row['id']}",
        )
        assert delete_other.status_code == status.HTTP_404_NOT_FOUND
