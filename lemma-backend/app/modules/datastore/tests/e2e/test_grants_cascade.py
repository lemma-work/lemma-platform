"""E2E regression tests for folder-grant path-prefix cascade (Workstream A).

These lock down the reported bug: a ``folder.read``/``folder.write`` grant on a
folder must cascade to its nested subfolders and RESTRICTED descendant files —
for read, list, write, and search. Pre-fix these FAIL (exact-id matching only);
post-fix they PASS. A non-granted sibling folder must remain denied.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status
from httpx import AsyncClient

from app.modules.test_support.e2e_authz import create_role_visibility_context

# Reuse the established HTTP wrapper from the shared harness.
from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e


async def _index_file(index_datastore_file, file_entity: dict) -> None:
    await index_datastore_file(UUID(file_entity["pod_id"]), UUID(file_entity["id"]))


class TestFolderGrantCascade:
    @pytest.mark.asyncio
    async def test_folder_grant_cascades_to_nested_restricted_descendants(
        self,
        authenticated_client: AsyncClient,
        async_client: AsyncClient,
        fixed_test_org,
        index_datastore_file,
    ):
        ctx = await create_role_visibility_context(
            authenticated_client,
            async_client,
            fixed_test_org,
            pod_name_prefix="datastore-cascade",
            custom_role="CASCADE_OPERATORS",
        )
        pod_id = ctx["pod_id"]
        owner = DatastoreApi(authenticated_client, pod_id)
        operator = DatastoreApi(async_client, pod_id, ctx["custom_viewer"])

        suffix = uuid4().hex[:8]
        root = f"/granted-{suffix}"
        sub = f"{root}/sub"
        sub2 = f"{sub}/sub2"
        leaf_token = f"ZZNeedle{suffix}"
        sibling = f"/sibling-{suffix}"

        # Build a fully RESTRICTED nested tree under the granted root.
        await owner.create_folder(root, visibility="RESTRICTED")
        await owner.create_folder(sub, visibility="RESTRICTED")
        await owner.create_folder(sub2, visibility="RESTRICTED")
        leaf = await owner.upload_file(
            "leaf.md",
            f"{leaf_token} restricted descendant body".encode(),
            directory_path=sub2,
            visibility="RESTRICTED",
        )
        await _index_file(index_datastore_file, leaf)
        assert leaf["visibility"] == "RESTRICTED"

        # A RESTRICTED sibling that will NOT be granted.
        sibling_folder = await owner.create_folder(sibling, visibility="RESTRICTED")

        # --- Pre-grant: operator is denied everything under the tree. ---
        await operator.get_file(root, expected_status=status.HTTP_403_FORBIDDEN)
        await operator.get_file(sub2, expected_status=status.HTTP_403_FORBIDDEN)
        await operator.get_file(leaf["path"], expected_status=status.HTTP_403_FORBIDDEN)
        pre_search = await operator.search_files(leaf_token)
        assert leaf["id"] not in {r["file_id"] for r in pre_search["items"]}

        # --- Grant folder.read + folder.write on the ROOT only. ---
        grant = await authenticated_client.put(
            f"/pods/{pod_id}/roles/{ctx['custom_role']}/permissions",
            json={
                "grants": [
                    {
                        "resource_type": "folder",
                        "resource_name": root,
                        "permission_ids": ["folder.read", "folder.write"],
                    }
                ]
            },
        )
        assert grant.status_code == status.HTTP_200_OK, grant.text

        # --- Cascade READ to nested subfolders + RESTRICTED deep file. ---
        assert (await operator.get_file(sub))["path"] == sub
        assert (await operator.get_file(sub2))["path"] == sub2
        granted_leaf = await operator.get_file(leaf["path"])
        assert granted_leaf["id"] == leaf["id"]
        assert "folder.read" in granted_leaf["allowed_actions"]

        # --- Cascade LIST at each level. ---
        root_listing = await operator.list_files(directory_path=root)
        assert any(item["path"] == sub for item in root_listing["items"])
        sub2_listing = await operator.list_files(directory_path=sub2)
        assert leaf["id"] in {item["id"] for item in sub2_listing["items"]}

        # --- Cascade SEARCH reaches the RESTRICTED descendant. ---
        post_search = await operator.search_files(leaf_token)
        assert leaf["id"] in {r["file_id"] for r in post_search["items"]}

        # --- Cascade WRITE into a deep granted subfolder. ---
        created = await operator.upload_file(
            "operator-created.md",
            b"written via cascaded folder.write",
            directory_path=sub2,
            search_enabled=False,
        )
        assert created["path"] == f"{sub2}/operator-created.md"

        # --- The non-granted sibling stays denied (no over-reach). ---
        await operator.get_file(sibling, expected_status=status.HTTP_403_FORBIDDEN)
        root_top = await operator.list_files(directory_path="/")
        assert sibling_folder["id"] not in {i["id"] for i in root_top["items"]}

    @pytest.mark.asyncio
    async def test_document_type_grant_cascades_like_folder(
        self,
        authenticated_client: AsyncClient,
        async_client: AsyncClient,
        fixed_test_org,
    ):
        """A grant stored under resource_type=document must cascade identically
        (FOLDER/DOCUMENT are authorization aliases)."""
        ctx = await create_role_visibility_context(
            authenticated_client,
            async_client,
            fixed_test_org,
            pod_name_prefix="datastore-cascade-doc",
            custom_role="DOC_OPERATORS",
        )
        pod_id = ctx["pod_id"]
        owner = DatastoreApi(authenticated_client, pod_id)
        operator = DatastoreApi(async_client, pod_id, ctx["custom_viewer"])

        suffix = uuid4().hex[:8]
        root = f"/docgrant-{suffix}"
        await owner.create_folder(root, visibility="RESTRICTED")
        child = await owner.upload_file(
            "child.md",
            b"doc-type grant body",
            directory_path=root,
            visibility="RESTRICTED",
            search_enabled=False,
        )

        await operator.get_file(
            child["path"], expected_status=status.HTTP_403_FORBIDDEN
        )

        grant = await authenticated_client.put(
            f"/pods/{pod_id}/roles/{ctx['custom_role']}/permissions",
            json={
                "grants": [
                    {
                        "resource_type": "document",
                        "resource_name": root,
                        "permission_ids": ["folder.read"],
                    }
                ]
            },
        )
        assert grant.status_code == status.HTTP_200_OK, grant.text

        granted_child = await operator.get_file(child["path"])
        assert granted_child["id"] == child["id"]
