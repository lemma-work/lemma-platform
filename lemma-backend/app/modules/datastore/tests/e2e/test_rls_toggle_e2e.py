"""E2E: toggling row-level security on an existing table.

RLS can only be flipped while the table is empty. Enabling installs the
per-user isolation column + policy; disabling removes the policy and lets all
pod members read every row.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import status

from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e


class TestRlsToggle:
    @pytest.mark.asyncio
    async def test_enable_rls_on_empty_shared_table(self, pod_api: DatastoreApi):
        name = f"toggle_on_{uuid4().hex[:8]}"
        await pod_api.create_table(
            {
                "name": name,
                "enable_rls": False,
                "columns": [{"name": "title", "type": "TEXT"}],
            }
        )
        updated = await pod_api.update_table(name, {"enable_rls": True})
        assert updated["enable_rls"] is True
        # The ownership column is now materialized.
        assert "user_id" in {c["name"] for c in updated["columns"]}

    @pytest.mark.asyncio
    async def test_disable_rls_on_empty_table(self, pod_api: DatastoreApi):
        name = f"toggle_off_{uuid4().hex[:8]}"
        await pod_api.create_table(
            {
                "name": name,
                "enable_rls": True,
                "columns": [{"name": "title", "type": "TEXT"}],
            }
        )
        updated = await pod_api.update_table(name, {"enable_rls": False})
        assert updated["enable_rls"] is False
        assert "user_id" not in {c["name"] for c in updated["columns"]}

    @pytest.mark.asyncio
    async def test_toggle_rls_rejected_on_non_empty_table(self, pod_api: DatastoreApi):
        name = f"toggle_full_{uuid4().hex[:8]}"
        await pod_api.create_table(
            {
                "name": name,
                "enable_rls": False,
                "columns": [{"name": "title", "type": "TEXT"}],
            }
        )
        await pod_api.create_record(name, {"title": "row"})
        result = await pod_api.update_table(
            name,
            {"enable_rls": True},
            expected_status=status.HTTP_400_BAD_REQUEST,
        )
        assert result["code"] == "DATASTORE_VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_enable_rls_then_rows_are_user_scoped(
        self,
        pod_api: DatastoreApi,
        async_client,
        member_users,
    ):
        editor_api = DatastoreApi(async_client, pod_api.pod_id, member_users["editor"])
        name = f"scoped_{uuid4().hex[:8]}"
        await pod_api.create_table(
            {
                "name": name,
                "enable_rls": False,
                "columns": [{"name": "title", "type": "TEXT"}],
            }
        )
        await pod_api.update_table(name, {"enable_rls": True})
        # Owner writes a row; another member must not see it under RLS.
        await pod_api.create_record(name, {"title": "owner-only"})
        editor_rows = await editor_api.list_records(name)
        assert all(r.get("title") != "owner-only" for r in editor_rows["items"])
