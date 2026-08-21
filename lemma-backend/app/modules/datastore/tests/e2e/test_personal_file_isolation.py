"""E2E tests: /me personal-file isolation across users in a pod.

`/me` is per-user sugar that the backend rewrites to the requester's own
`/{user_id}` subtree, and personal files are PERSONAL-visibility so only the
owner can read them. These tests pin that isolation AND guard against the new
folder-grant cascade / pod-wide `/` grant leaking one user's personal tree to
another.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status
from httpx import AsyncClient

from app.modules.test_support.e2e_authz import create_role_visibility_context

from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e


async def _index(index_datastore_file, file_entity: dict) -> None:
    await index_datastore_file(UUID(file_entity["pod_id"]), UUID(file_entity["id"]))


class TestPersonalFileIsolation:
    @pytest.mark.asyncio
    async def test_me_is_a_separate_personal_tree_per_user(
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
            pod_name_prefix="datastore-me-isolation",
            custom_role="ME_ISO",
        )
        pod_id = ctx["pod_id"]
        owner = DatastoreApi(authenticated_client, pod_id)
        other = DatastoreApi(async_client, pod_id, ctx["custom_viewer"])

        token = f"ZZSecret{uuid4().hex[:8]}"
        secret = await owner.upload_file(
            "secret.md",
            f"{token} personal-only body".encode(),
            directory_path="/me",
        )
        await _index(index_datastore_file, secret)

        # API path is /me/...; it resolves internally to the owner's /{user_id}.
        assert secret["path"] == "/me/secret.md"
        assert secret["visibility"] == "PERSONAL"
        owner_id = secret["owner_user_id"]
        internal_path = f"/{owner_id}/secret.md"

        # Owner reaches the file via BOTH /me and the internal id-path (same row).
        via_me = await owner.get_file("/me/secret.md")
        via_internal = await owner.get_file(internal_path)
        assert via_me["id"] == secret["id"] == via_internal["id"]

        # Another user's /me is a DIFFERENT tree → 404 for the same API path.
        await other.get_file("/me/secret.md", expected_status=status.HTTP_404_NOT_FOUND)

        # And the underlying isolation is real: reading the owner's internal
        # id-path is forbidden (PERSONAL, not owner), not merely hidden by /me.
        await other.get_file(internal_path, expected_status=status.HTTP_403_FORBIDDEN)
        await other.download_file(
            internal_path, expected_status=status.HTTP_403_FORBIDDEN
        )

        # Listing /me shows each user only their own (empty) personal tree.
        other_me = await other.list_files(directory_path="/me")
        assert secret["id"] not in {item["id"] for item in other_me["items"]}

        # Search by the other user never surfaces the personal file.
        other_search = await other.search_files(token)
        assert secret["id"] not in {r["file_id"] for r in other_search["items"]}

    @pytest.mark.asyncio
    async def test_pod_wide_grant_does_not_leak_personal_files(
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
            pod_name_prefix="datastore-me-podgrant",
            custom_role="POD_WIDE",
        )
        pod_id = ctx["pod_id"]
        owner = DatastoreApi(authenticated_client, pod_id)
        other = DatastoreApi(async_client, pod_id, ctx["custom_viewer"])

        token = f"ZZSecret{uuid4().hex[:8]}"
        secret = await owner.upload_file(
            "secret.md",
            f"{token} personal-only body".encode(),
            directory_path="/me",
        )
        await _index(index_datastore_file, secret)
        owner_id = secret["owner_user_id"]
        internal_path = f"/{owner_id}/secret.md"

        # A RESTRICTED pod file proves the pod-wide grant actually grants
        # something (so the personal-denial below isn't vacuous).
        restricted = await owner.create_folder(
            f"/locked-{uuid4().hex[:6]}", visibility="RESTRICTED"
        )
        restricted_doc = await owner.upload_file(
            "doc.md",
            b"restricted pod doc",
            directory_path=restricted["path"],
            visibility="RESTRICTED",
            search_enabled=False,
        )
        await other.get_file(
            restricted_doc["path"], expected_status=status.HTTP_403_FORBIDDEN
        )

        # Grant the whole pod ("/") to the other user's custom role.
        grant = await authenticated_client.put(
            f"/pods/{pod_id}/roles/{ctx['custom_role']}/permissions",
            json={
                "grants": [
                    {
                        "resource_type": "folder",
                        "resource_name": "/",
                        "permission_ids": ["folder.read", "folder.write"],
                    }
                ]
            },
        )
        assert grant.status_code == status.HTTP_200_OK, grant.text

        # The pod-wide grant DOES reach RESTRICTED pod documents...
        assert (await other.get_file(restricted_doc["path"]))["id"] == restricted_doc[
            "id"
        ]

        # ...but it must NOT reach another user's PERSONAL files, by any route.
        await other.get_file(internal_path, expected_status=status.HTTP_403_FORBIDDEN)
        await other.download_file(
            internal_path, expected_status=status.HTTP_403_FORBIDDEN
        )
        leaked = await other.search_files(token)
        assert secret["id"] not in {r["file_id"] for r in leaked["items"]}

        # The owner still reaches their own personal file.
        assert (await owner.get_file("/me/secret.md"))["id"] == secret["id"]
