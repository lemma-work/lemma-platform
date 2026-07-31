"""E2E tests for datastore file paths: personal vs pod roots, skills, tree ops.

Covers the shared file API across personal (/me) and pod roots, the native +
custom skills overlay, and tree pagination / rename / update / recursive
delete. Search and conversion live in ``test_search_conversion_e2e.py``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import status
from httpx import AsyncClient

from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e


class TestDatastoreFilePaths:
    @pytest.mark.asyncio
    async def test_personal_and_pod_files_share_one_api_but_have_separate_roots(
        self,
        pod_api: DatastoreApi,
        async_client: AsyncClient,
        member_users,
    ):
        """/me files are per-user while /pod files are shared; viewer reads but cannot write pod files."""
        viewer_api = DatastoreApi(async_client, pod_api.pod_id, member_users["viewer"])
        editor_api = DatastoreApi(async_client, pod_api.pod_id, member_users["editor"])

        personal_folder = await pod_api.create_folder("/me/briefs")
        personal_file = await pod_api.upload_file(
            "summary.md",
            b"owner private artifact",
            directory_path=personal_folder["path"],
            search_enabled=True,
        )
        assert personal_file["path"] == "/me/briefs/summary.md"
        assert personal_file["search_enabled"] is True
        assert personal_file["status"] == "PENDING"

        viewer_same_path = await viewer_api.create_folder("/me/briefs")
        viewer_file = await viewer_api.upload_file(
            "summary.md",
            b"viewer private artifact",
            directory_path=viewer_same_path["path"],
            search_enabled=True,
        )
        assert viewer_file["path"] == personal_file["path"]
        assert viewer_file["owner_user_id"] != personal_file["owner_user_id"]

        pod_folder = await pod_api.create_folder("/briefs")
        pod_file = await pod_api.upload_file(
            "summary.md",
            b"shared pod artifact",
            directory_path=pod_folder["path"],
            search_enabled=True,
        )
        assert pod_file["path"] == "/briefs/summary.md"
        assert pod_file["search_enabled"] is True

        owner_personal = await pod_api.list_files(directory_path="/me")
        viewer_personal = await viewer_api.list_files(directory_path="/me")
        pod_listing = await viewer_api.list_files()
        assert {item["name"] for item in owner_personal["items"]} == {"briefs"}
        assert {item["name"] for item in viewer_personal["items"]} == {"briefs"}
        # The pod root surfaces the synthetic /me and /skills folders by default.
        assert {item["name"] for item in pod_listing["items"]} == {
            "me",
            "skills",
            "briefs",
        }
        me_folder = next(item for item in pod_listing["items"] if item["name"] == "me")
        assert me_folder["path"] == "/me"
        assert me_folder["kind"] == "FOLDER"
        skills_folder = next(
            item for item in pod_listing["items"] if item["name"] == "skills"
        )
        assert skills_folder["path"] == "/skills"
        assert skills_folder["kind"] == "FOLDER"

        assert (
            await pod_api.download_file(personal_file["path"])
            == b"owner private artifact"
        )
        assert (
            await viewer_api.download_file(personal_file["path"])
            == b"viewer private artifact"
        )
        assert (
            await viewer_api.download_file(pod_file["path"]) == b"shared pod artifact"
        )

        viewer_pod_upload = await viewer_api.upload_file(
            "viewer.md",
            b"viewer cannot write shared files",
            expected_status=status.HTTP_403_FORBIDDEN,
        )
        assert viewer_pod_upload["code"] == "INSUFFICIENT_PERMISSION"

        editor_pod_file = await editor_api.upload_file(
            "editor.md",
            b"editor can write shared files",
            search_enabled=True,
        )
        assert editor_pod_file["path"] == "/editor.md"

        editor_delete_owner_pod = await editor_api.delete_file(
            pod_file["path"],
            expected_status=status.HTTP_403_FORBIDDEN,
        )
        assert editor_delete_owner_pod["code"] == "INSUFFICIENT_PERMISSION"

        await pod_api.delete_file(pod_file["path"])
        await editor_api.delete_file(editor_pod_file["path"])

    @pytest.mark.asyncio
    async def test_skills_path_serves_native_skills_and_custom_pod_skills_read_only_for_native(
        self,
        pod_api: DatastoreApi,
    ):
        """/skills overlays read-only native skills with writable custom pod skills."""
        custom_skill_name = f"e2e-skill-{uuid4().hex[:8]}"
        await pod_api.create_folder(f"/skills/{custom_skill_name}")
        custom_skill_file = await pod_api.upload_file(
            "SKILL.md",
            b"---\nname: e2e skill\n---\n# E2E Skill\n",
            directory_path=f"/skills/{custom_skill_name}",
        )

        skills_listing = await pod_api.list_files(directory_path="/skills", limit=1000)
        skill_paths = {item["path"] for item in skills_listing["items"]}
        assert "/skills/browser" in skill_paths
        assert f"/skills/{custom_skill_name}" in skill_paths

        native_skill = await pod_api.get_file("/skills/browser/SKILL.md")
        assert native_skill["path"] == "/skills/browser/SKILL.md"
        assert native_skill["metadata"]["read_only"] is True
        assert b"name: browser" in await pod_api.download_file(native_skill["path"])

        blocked_native_write = await pod_api.upload_file(
            "EXTRA.md",
            b"native skill write should be blocked",
            directory_path="/skills/browser",
            expected_status=status.HTTP_400_BAD_REQUEST,
        )
        assert "read-only" in blocked_native_write["message"]

        assert await pod_api.download_file(custom_skill_file["path"]) == (
            b"---\nname: e2e skill\n---\n# E2E Skill\n"
        )

    @pytest.mark.asyncio
    async def test_file_tree_pagination_rename_update_and_recursive_delete(
        self,
        pod_api: DatastoreApi,
    ):
        """Folders paginate and tree-render; a file renames+updates and a folder deletes recursively."""
        await pod_api.create_folder("/me/research")
        await pod_api.create_folder("/me/research/transformers")
        await pod_api.create_folder("/me/operations")
        await pod_api.upload_file(
            "a.md", b"a", directory_path="/me/research/transformers"
        )
        await pod_api.upload_file(
            "b.md", b"b", directory_path="/me/research/transformers"
        )

        first_page = await pod_api.list_files(directory_path="/me", limit=1)
        assert first_page["next_page_token"]
        second_page = await pod_api.list_files(
            directory_path="/me",
            limit=10,
            page_token=first_page["next_page_token"],
        )
        assert {
            item["name"] for item in first_page["items"] + second_page["items"]
        } == {
            "research",
            "operations",
        }

        tree = await pod_api.tree(root_path="/me/research", files_per_directory=1)
        assert tree["tree"]["path"] == "/me/research"
        assert tree["tree"]["children"][0]["name"] == "transformers"

        renamed = await pod_api.update_file(
            "/me/research/transformers/b.md",
            new_path="/me/operations/renamed.md",
            content=b"renamed content",
            filename="renamed.md",
            search_enabled=True,
        )
        assert renamed["path"] == "/me/operations/renamed.md"
        assert renamed["search_enabled"] is True
        assert (
            await pod_api.download_file("/me/operations/renamed.md")
            == b"renamed content"
        )

        await pod_api.delete_file("/me/research")
        await pod_api.get_file(
            "/me/research", expected_status=status.HTTP_404_NOT_FOUND
        )
        await pod_api.get_file(
            "/me/research/transformers/a.md",
            expected_status=status.HTTP_404_NOT_FOUND,
        )
        surviving = await pod_api.get_file("/me/operations/renamed.md")
        assert surviving["name"] == "renamed.md"


@pytest.mark.asyncio
async def test_file_download_round_trips_content_and_sets_disposition(
    pod_api: DatastoreApi,
):
    """Regression (DB pool exhaustion): the streaming download endpoint resolves
    in a short UoW and streams the bytes only after the pooled connection is
    released. The round-trip content and the Content-Disposition header must
    still be intact end-to-end."""
    uploaded = await pod_api.upload_file(
        "download-check.md", b"hello download", directory_path="/"
    )

    response = await pod_api.request(
        "GET",
        f"/pods/{pod_api.pod_id}/datastore/files/download",
        params={"path": uploaded["path"]},
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.content == b"hello download"
    assert "filename" in response.headers.get("content-disposition", "")
    etag = f'"{uploaded["content_sha256"]}"'
    assert response.headers["etag"] == etag
    assert response.headers["cache-control"] == "private, no-cache"

    not_modified = await pod_api.request(
        "GET",
        f"/pods/{pod_api.pod_id}/datastore/files/download",
        params={"path": uploaded["path"]},
        headers={"If-None-Match": f"W/{etag}"},
    )
    assert not_modified.status_code == status.HTTP_304_NOT_MODIFIED
    assert not not_modified.content
    assert not_modified.headers["etag"] == etag


@pytest.mark.asyncio
async def test_folder_move_copies_descendant_originals(pod_api: DatastoreApi):
    await pod_api.create_folder("/me/move-source")
    await pod_api.create_folder("/me/move-source/nested")
    uploaded = await pod_api.upload_file(
        "inside.md",
        b"move me",
        directory_path="/me/move-source/nested",
    )

    moved = await pod_api.update_file(
        "/me/move-source", new_path="/me/move-target"
    )

    assert moved["path"] == "/me/move-target"
    assert (
        await pod_api.download_file("/me/move-target/nested/inside.md")
        == b"move me"
    )
    await pod_api.get_file(uploaded["path"], expected_status=status.HTTP_404_NOT_FOUND)
