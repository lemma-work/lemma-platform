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

from app.modules.datastore.config import datastore_settings
from app.modules.test_support.e2e_authz import create_role_visibility_context

from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e


def _ids(search_result: dict) -> set[str]:
    return {item["file_id"] for item in search_result.get("items", [])}


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


class TestAgentMemoryIsolation:
    """An agent's memory splits across two trees, and the split is the point.

    `/memory` is what the pod knows and every member should see. `/me` is what
    the agent knows about *one person* — their name, their preferences, what
    they told it in confidence — and nobody else should. Both are ordinary
    datastore files, so the split rests entirely on path-derived visibility.
    This pins both directions: the shared one really is shared, and the private
    one really is private.
    """

    @pytest.mark.asyncio
    async def test_pod_memory_is_readable_by_another_member(
        self,
        authenticated_client: AsyncClient,
        async_client: AsyncClient,
        fixed_test_org,
    ):
        ctx = await create_role_visibility_context(
            authenticated_client,
            async_client,
            fixed_test_org,
            pod_name_prefix="agent-memory-shared",
            custom_role="MEM_SHARED",
        )
        pod_id = ctx["pod_id"]
        owner = DatastoreApi(authenticated_client, pod_id)
        other = DatastoreApi(async_client, pod_id, ctx["custom_viewer"])

        await owner.create_folder("/memory")
        shared = await owner.upload_file(
            "AGENTS.md", b"- pod-wide fact", directory_path="/memory"
        )

        assert shared["visibility"] == "POD"
        # The other member reads the same row, not a copy of their own.
        assert (await other.get_file("/memory/AGENTS.md"))["id"] == shared["id"]

    @pytest.mark.asyncio
    async def test_personal_agent_memory_is_not_readable_by_another_member(
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
            pod_name_prefix="agent-memory-personal",
            custom_role="MEM_PERSONAL",
        )
        pod_id = ctx["pod_id"]
        owner = DatastoreApi(authenticated_client, pod_id)
        other = DatastoreApi(async_client, pod_id, ctx["custom_viewer"])

        token = f"ZZMemory{uuid4().hex[:8]}"
        await owner.create_folder("/me/agents")
        await owner.create_folder("/me/agents/butler")
        note = await owner.upload_file(
            "AGENTS.md",
            f"{token} what the agent knows about this person".encode(),
            directory_path="/me/agents/butler",
        )
        await _index(index_datastore_file, note)

        assert note["visibility"] == "PERSONAL"

        # Positive control. Without one, every denial below passes just as well
        # when the other member has lost all access to the pod — the sibling
        # test guards its own vacuity the same way.
        shared = await owner.upload_file(
            "readable.md", b"pod-visible", directory_path="/"
        )
        assert (await other.get_file("/readable.md"))["id"] == shared["id"]

        # A different member's `/me` is a different tree entirely.
        await other.get_file(
            "/me/agents/butler/AGENTS.md",
            expected_status=status.HTTP_404_NOT_FOUND,
        )

        # And the isolation is real, not an alias trick: the expanded path is
        # spellable, and reaching for it is refused rather than merely hidden.
        internal = f"/{note['owner_user_id']}/agents/butler/AGENTS.md"
        await other.get_file(internal, expected_status=status.HTTP_403_FORBIDDEN)

        # Nor can they find it by searching for something only it contains.
        found = await other.search_files(token)
        assert note["id"] not in {item["file_id"] for item in found["items"]}


class TestSearchScopeBranches:
    """Search narrows its chunk query two ways, and both must hide the same files.

    Chunks live in a database that holds no authorization data and cannot be
    joined to the file table, so the answer travels as an array of ids. Search
    used to build that array by reading *every* file row in the pod, on every
    query. It now sends the array only while it is short enough to send, and
    otherwise runs the chunk query unnarrowed and authorizes the rows that come
    back.

    Which branch runs is decided by one number in configuration, so the two are
    a pair that can drift apart without anything failing: the fast one is the
    one that ships, and the other is reached only by pods large enough that
    nobody is running the test suite against them. Flipping the ceiling is the
    whole point of these tests.
    """

    @pytest.mark.asyncio
    async def test_both_branches_return_the_same_results(
        self,
        authenticated_client: AsyncClient,
        async_client: AsyncClient,
        fixed_test_org,
        index_datastore_file,
        monkeypatch,
    ):
        ctx = await create_role_visibility_context(
            authenticated_client,
            async_client,
            fixed_test_org,
            pod_name_prefix="datastore-scope-branches",
            custom_role="SCOPE_BRANCH",
        )
        pod_id = ctx["pod_id"]
        owner = DatastoreApi(authenticated_client, pod_id)
        other = DatastoreApi(async_client, pod_id, ctx["custom_viewer"])

        token = f"ZZBranch{uuid4().hex[:8]}"
        shared = await owner.upload_file(
            "shared-note.md",
            f"{token} readable by the whole pod".encode(),
            directory_path="/",
        )
        personal = await owner.upload_file(
            "private-note.md",
            f"{token} readable by nobody else".encode(),
            directory_path="/me",
        )
        await _index(index_datastore_file, shared)
        await _index(index_datastore_file, personal)

        # Enumerated: the readable set fits under the ceiling and goes into the
        # chunk query, so the hidden file is never a candidate.
        monkeypatch.setattr(
            datastore_settings, "datastore_search_readable_id_pushdown_limit", 10_000
        )
        enumerated = await other.search_files(token)

        # Post-filtered: the ceiling is below the readable set, so the chunk
        # query runs unnarrowed, returns the hidden file among its candidates,
        # and the authorization read is the only thing that drops it.
        monkeypatch.setattr(
            datastore_settings, "datastore_search_readable_id_pushdown_limit", 0
        )
        post_filtered = await other.search_files(token)

        assert _ids(enumerated) == {shared["id"]}, enumerated
        assert _ids(post_filtered) == _ids(enumerated), (
            "the two branches disagree about what this caller may find:\n"
            f"  only enumerated:    {_ids(enumerated) - _ids(post_filtered)}\n"
            f"  only post-filtered: {_ids(post_filtered) - _ids(enumerated)}"
        )
        assert personal["id"] not in _ids(post_filtered), (
            "an unnarrowed chunk query returned another user's personal file "
            "and nothing dropped it"
        )

        # The owner still finds both, whichever branch runs -- a filter that
        # hid everything would satisfy the equality above.
        assert _ids(await owner.search_files(token)) == {shared["id"], personal["id"]}
