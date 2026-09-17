"""E2E tests for datastore file paths: personal vs pod roots, skills, tree ops.

Covers the shared file API across personal (/me) and pod roots, the native +
custom skills overlay, and tree pagination / rename / update / recursive
delete. Search and conversion live in ``test_search_conversion_e2e.py``.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status
from httpx import AsyncClient

from app.modules.datastore.tests.e2e.harness import DatastoreApi


async def _stored_path(db_session, file_id: str) -> str:
    """The path as the table holds it, not as the API spells it.

    `/me/x` is per-request sugar for `/{owner}/x`, and the rename statements
    run on stored paths. A test that passes the API spelling to them matches
    nothing, which looks like a pass for every assertion that expects a
    refusal.
    """
    from sqlalchemy import select

    from app.modules.datastore.infrastructure.models import DatastoreFile

    result = await db_session.execute(
        select(DatastoreFile.path).where(DatastoreFile.id == UUID(file_id))
    )
    return result.scalars().one()


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
    async def test_listing_one_skill_folder_does_not_read_the_others(
        self,
        pod_api: DatastoreApi,
    ):
        """`PS-DATA-031`: list a folder's contents without loading the tree.

        The overlay used to read the whole `/skills` subtree and keep the rows
        whose parent matched the directory asked for -- so opening one skill
        folder read every file of every other skill in the pod, and the listing
        got slower as unrelated skills were added.

        The result was always right -- the Python filter returned exactly these
        items, which is what made the cost invisible -- so this end of it checks
        that narrowing the read did not change the answer, with a neighbour
        holding files that must neither appear nor be fetched. That the subtree
        read is gone at all is asserted on the call itself in
        ``tests/unit/test_file_service.py``.
        """
        mine = f"e2e-skill-{uuid4().hex[:8]}"
        neighbour = f"e2e-skill-{uuid4().hex[:8]}"
        await pod_api.create_folder(f"/skills/{mine}")
        await pod_api.create_folder(f"/skills/{neighbour}")
        await pod_api.upload_file(
            "SKILL.md", b"---\nname: mine\n---\n", directory_path=f"/skills/{mine}"
        )
        for index in range(5):
            await pod_api.upload_file(
                f"note-{index}.md",
                b"noise",
                directory_path=f"/skills/{neighbour}",
            )

        listing = await pod_api.list_files(directory_path=f"/skills/{mine}", limit=1000)

        assert {item["name"] for item in listing["items"]} == {"SKILL.md"}, (
            "the neighbour's files must not appear -- and must not have been read"
        )

    @pytest.mark.asyncio
    async def test_a_skill_tree_does_not_read_the_files_it_will_not_show(
        self,
        pod_api: DatastoreApi,
    ):
        """The second half of `PS-DATA-031`, which rooting the query left undone.

        A tree shows every folder but caps files at `files_per_directory` in
        each one, so its answer is O(folders x cap) however many files exist.
        The overlay first read all of `/skills` and kept the rows under the
        requested root; narrowing it to the root fixed which *skill* was read
        and not how much of it -- every file beneath that root was still loaded
        for Python to slice three off the front.

        `tree_statements` is where the cap belongs and where the ordinary
        directory tree already puts it: files ranked within their own directory
        by a window function and cut at one more than will be shown. Asserting
        the window is in the statement is asserting the cap is in the database
        -- a Python slice over a full read produces the same answer, which is
        exactly why this went unnoticed the first time.
        """
        from app.modules.test_support.query_counting import (
            counted_queries,
            format_statements,
            statements_touching,
        )

        skill = f"e2e-skill-{uuid4().hex[:8]}"
        await pod_api.create_folder(f"/skills/{skill}")
        await pod_api.upload_file(
            "SKILL.md", b"---\nname: mine\n---\n", directory_path=f"/skills/{skill}"
        )
        for index in range(12):
            await pod_api.upload_file(
                f"attachment-{index:02d}.md",
                b"body",
                directory_path=f"/skills/{skill}",
            )

        with counted_queries() as statements:
            response = await pod_api.tree(
                root_path=f"/skills/{skill}", files_per_directory=3
            )

        tree = response["tree"]
        shown = [child for child in tree["children"] if child["kind"] != "FOLDER"]
        assert len(shown) == 3, tree
        assert tree["has_more_files"] is True, (
            "thirteen files were capped at three and the caller was not told"
        )

        file_reads = statements_touching(statements, "datastore_files")
        assert any("row_number" in statement.lower() for statement in file_reads), (
            "no statement ranked files within their directory, so the cap is "
            "still a Python slice over every file under the root:\n"
            + format_statements(file_reads)
        )

    @pytest.mark.asyncio
    async def test_renaming_a_folder_costs_the_same_whatever_is_in_it(
        self,
        pod_api: DatastoreApi,
        db_session,
    ):
        """Two defects, one operation, and neither was visible in the result.

        The paths were rewritten a row at a time -- and `update()` reads a row
        before writing it, so a folder of N files issued 2N statements inside the
        request transaction. And every one of those rows was marked for
        reprocessing, so renaming a folder re-extracted every document under it,
        OCR included, to produce artifacts identical to the ones the same
        operation had just deleted.

        Asserted differentially: the statement count must not move between a
        folder of one file and a folder of eight, and no file may come back
        PENDING. An absolute budget would miss the second half, because a
        re-extraction is not a statement.
        """
        from sqlalchemy import select

        from app.modules.datastore.infrastructure.models import DatastoreFile
        from app.modules.test_support.query_counting import counted_queries

        async def rename_folder_of(count: int, tag: str) -> int:
            await pod_api.create_folder(f"/me/{tag}")
            for index in range(count):
                await pod_api.upload_file(
                    f"doc-{index}.md", b"# doc", directory_path=f"/me/{tag}"
                )
            with counted_queries() as statements:
                await pod_api.update_file(f"/me/{tag}", new_path=f"/me/{tag}-renamed")
            return len([text for text in statements if "datastore_files" in text])

        small = await rename_folder_of(1, f"small-{uuid4().hex[:6]}")
        large = await rename_folder_of(8, f"large-{uuid4().hex[:6]}")

        assert small == large, (
            f"renaming one file cost {small} statements and eight cost {large}"
        )
        # Not vacuous: the rename does touch the table, so equality is a real
        # claim about growth rather than about nothing having happened.
        assert small > 0

        pending = (
            (
                await db_session.execute(
                    select(DatastoreFile.path).where(
                        DatastoreFile.path.like("/me/%-renamed/%"),
                        DatastoreFile.status == "PENDING",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert pending == [], f"a rename queued these for re-extraction: {pending}"

    @pytest.mark.asyncio
    async def test_a_file_uploaded_mid_rename_does_not_get_a_path_to_nowhere(
        self,
        pod_api: DatastoreApi,
        db_session,
    ):
        """The race the one-statement rewrite would otherwise make silent.

        The copy plan is taken before the storage phase and the bytes are copied
        from it. A file uploaded into the folder in between was never copied, so
        it must not be repointed -- and it must not be left under a folder that
        no longer exists either, which is the same corruption facing the other
        way. Refusing is the only answer that is neither, and this is that
        refusal.
        """
        from app.modules.datastore.domain.errors import DatastoreConflictError
        from app.modules.datastore.infrastructure.repositories.file_repository import (
            DatastoreFileRepository,
        )
        from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

        tag = f"race-{uuid4().hex[:6]}"
        folder = await pod_api.create_folder(f"/me/{tag}")
        planned = await pod_api.upload_file(
            "planned.md", b"planned", directory_path=f"/me/{tag}"
        )
        # The plan saw one descendant; a second arrives before the rewrite runs.
        await pod_api.upload_file("late.md", b"late", directory_path=f"/me/{tag}")

        # The *stored* paths, not the `/me` spelling the API answers with: the
        # table holds `/{owner}/...`, and the rename runs on entity paths. The
        # earlier version of this test passed the API path, so its `LIKE`
        # matched nothing and the refusal it asserted was the empty one.
        folder_path = await _stored_path(db_session, folder["id"])
        planned_path = await _stored_path(db_session, planned["id"])

        repository = DatastoreFileRepository(SqlAlchemyUnitOfWork(db_session))
        with pytest.raises(DatastoreConflictError, match="gained an entry"):
            await repository.rewrite_descendant_paths(
                UUID(pod_api.pod_id),
                previous_prefix=folder_path,
                new_prefix=f"{folder_path}-renamed",
                planned=[(UUID(planned["id"]), planned_path)],
            )
        await db_session.rollback()

        still_there = await pod_api.list_files(directory_path=f"/me/{tag}", limit=100)
        assert {item["name"] for item in still_there["items"]} == {
            "planned.md",
            "late.md",
        }, "the refusal has to leave the folder exactly as it was"

    @pytest.mark.asyncio
    async def test_a_child_renamed_mid_rename_does_not_get_a_path_to_nowhere(
        self,
        pod_api: DatastoreApi,
        db_session,
    ):
        """The same race, in the shape a row count cannot see.

        One child renamed inside the folder while the copy runs takes a row out
        of the plan's set and puts a different one in. The number of rows under
        the old prefix never changes -- so the count check this used to rely on
        stayed silent, while the renamed row was repointed to a path whose bytes
        were copied from the name it no longer has.

        The fence is the ``(id, path)`` pair now, and this asserts the count is
        genuinely uninformative here: it still agrees, and the rename is still
        refused.
        """
        from app.modules.datastore.domain.errors import DatastoreConflictError
        from app.modules.datastore.infrastructure.repositories.file_repository import (
            DatastoreFileRepository,
        )
        from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
        from sqlalchemy import func, select
        from app.modules.datastore.infrastructure.models import DatastoreFile

        tag = f"swap-{uuid4().hex[:6]}"
        folder = await pod_api.create_folder(f"/me/{tag}")
        staged = await pod_api.upload_file(
            "before.md", b"contents", directory_path=f"/me/{tag}"
        )
        folder_path = await _stored_path(db_session, folder["id"])
        # What the copy plan captured, taken before the child moves.
        planned = [(UUID(staged["id"]), await _stored_path(db_session, staged["id"]))]

        # The child is renamed within the same folder while the copy runs. Its
        # bytes now live under `after.md`; the plan copied `before.md`.
        await pod_api.update_file(staged["path"], new_path=f"/me/{tag}/after.md")

        under_old_prefix = (
            (
                await db_session.execute(
                    select(func.count())
                    .select_from(DatastoreFile)
                    .where(DatastoreFile.path.like(f"{folder_path}/%"))
                )
            )
            .scalars()
            .one()
        )
        assert under_old_prefix == len(planned), (
            "the fixture did not reproduce a swap -- if the row count moved, "
            "the old count check would have caught this and the test proves "
            "nothing about the pair"
        )

        repository = DatastoreFileRepository(SqlAlchemyUnitOfWork(db_session))
        with pytest.raises(DatastoreConflictError, match="still held the path"):
            await repository.rewrite_descendant_paths(
                UUID(pod_api.pod_id),
                previous_prefix=folder_path,
                new_prefix=f"{folder_path}-renamed",
                planned=planned,
            )
        await db_session.rollback()

        still_there = await pod_api.list_files(directory_path=f"/me/{tag}", limit=100)
        assert {item["name"] for item in still_there["items"]} == {"after.md"}, (
            "the refusal has to leave the folder exactly as it was"
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

    moved = await pod_api.update_file("/me/move-source", new_path="/me/move-target")

    assert moved["path"] == "/me/move-target"
    assert await pod_api.download_file("/me/move-target/nested/inside.md") == b"move me"
    await pod_api.get_file(uploaded["path"], expected_status=status.HTTP_404_NOT_FOUND)
