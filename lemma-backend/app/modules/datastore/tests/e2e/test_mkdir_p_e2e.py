"""E2E: ``mkdir -p`` semantics for file/folder writes.

Creating a folder (or uploading a file) at a deep path auto-creates any missing
parent folders, under both the personal ``/me`` root and pod-shared roots, with
each level keeping the visibility its path implies.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e


class TestMkdirP:
    @pytest.mark.asyncio
    async def test_me_deep_folder_creates_missing_parents(self, pod_api: DatastoreApi):
        z = await pod_api.create_folder("/me/x/y/z")
        assert z["path"] == "/me/x/y/z"
        # Intermediate folders now exist and are personal.
        listing = await pod_api.list_files(directory_path="/me")
        assert "x" in {item["name"] for item in listing["items"]}
        mid = await pod_api.list_files(directory_path="/me/x/y")
        assert "z" in {item["name"] for item in mid["items"]}

    @pytest.mark.asyncio
    async def test_me_deep_upload_creates_missing_parents(self, pod_api: DatastoreApi):
        up = await pod_api.upload_file(
            "deep.md", b"hi", directory_path="/me/p/q/r", search_enabled=False
        )
        assert up["path"] == "/me/p/q/r/deep.md"
        assert up["visibility"] == "PERSONAL"

    @pytest.mark.asyncio
    async def test_pod_deep_folder_creates_missing_parents(self, pod_api: DatastoreApi):
        leaf = await pod_api.create_folder("/projects/2026/q3")
        assert leaf["path"] == "/projects/2026/q3"
        assert leaf["visibility"] == "POD"

    @pytest.mark.asyncio
    async def test_concurrent_uploads_share_new_parent_without_conflicts(
        self, pod_api: DatastoreApi
    ):
        uploads = await asyncio.gather(
            *(
                pod_api.upload_file(
                    f"book-{index}.pdf",
                    b"digital pdf fixture",
                    directory_path="/concurrent/books",
                    search_enabled=False,
                )
                for index in range(5)
            )
        )

        assert {item["name"] for item in uploads} == {
            f"book-{index}.pdf" for index in range(5)
        }

    @pytest.mark.asyncio
    async def test_skills_custom_deep_folder_creates_missing_parents(
        self, pod_api: DatastoreApi
    ):
        leaf = await pod_api.create_folder("/skills/my-skill/examples")
        assert leaf["path"] == "/skills/my-skill/examples"

    @pytest.mark.asyncio
    async def test_me_autocreated_parents_stay_private_to_owner(
        self,
        pod_api: DatastoreApi,
        async_client: AsyncClient,
        member_users,
    ):
        editor_api = DatastoreApi(async_client, pod_api.pod_id, member_users["editor"])
        await pod_api.create_folder("/me/secret/inner")
        # Another member's /me is their own; they never see the owner's tree.
        editor_me = await editor_api.list_files(directory_path="/me")
        assert "secret" not in {item["name"] for item in editor_me["items"]}
