"""Holistic workload permissions: folders / files.

Covers, for both AGENT and FUNCTION workloads, that a folder grant behaves as
documented for read, write, list, and search:

* a ``folder.read`` grant on a parent cascades to descendant files/subfolders
  (read + list + search), but never confers write;
* a ``folder.write`` grant cascades to descendants for write (and implies read);
* search is grant-gated even when a ``scope_path`` is passed — no grant means a
  scoped search is denied and a global search returns nothing;
* grants do NOT cascade UP (a grant on a child folder gives no access to a
  sibling under the shared parent), i.e. no over-reach;
* the DEFAULT POD AGENT ("user-resolved" mode) reaches the invoking user's pod
  files with no per-resource workload grant at all.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.modules.pod.tests.e2e.workload_permissions.harness import (
    AGENT,
    FUNCTION,
    DatastoreApi,
    create_pod,
    create_workload,
    mint_default_pod_agent_client,
    mint_workload_client,
    replace_workload_grants,
)

pytestmark = pytest.mark.e2e

NEEDLE = "ZZWorkloadFolderNeedle"
LIBRARY = "/library"
REVISION = "/library/revision"
DEEP = "/library/revision/deep"
LEAF = "/library/revision/algorithm.md"
LIBRARY_OTHER = "/library/other.md"
PRIVATE = "/private"
PRIVATE_FILE = "/private/secret.md"


def _folder_grant(path: str, *permissions: str) -> dict:
    return {
        "resource_type": "folder",
        "resource_name": path,
        "permission_ids": list(permissions),
    }


async def _seed_tree(owner: DatastoreApi, index_datastore_file) -> dict:
    """Build the shared pod-visible library tree + an ungranted sibling.

    ``/library/revision/algorithm.md`` (indexed, searchable) and a second file
    ``/library/other.md`` directly under the granted root; ``/private/secret.md``
    is the never-granted sibling.
    """
    await owner.create_folder(LIBRARY)
    leaf = await owner.upload_file(
        "algorithm.md",
        f"{NEEDLE} severe correction algorithm body".encode(),
        directory_path=REVISION,
        search_enabled=True,
    )
    assert leaf["path"] == LEAF and leaf["visibility"] == "POD"
    await index_datastore_file(UUID(leaf["pod_id"]), UUID(leaf["id"]))

    other = await owner.upload_file(
        "other.md",
        b"sibling under library",
        directory_path=LIBRARY,
        search_enabled=False,
    )
    sibling = await owner.upload_file(
        "secret.md",
        b"private not granted",
        directory_path=PRIVATE,
        search_enabled=False,
    )
    revision = await owner.get_file(REVISION)
    return {"leaf": leaf, "other": other, "sibling": sibling, "revision": revision}


# --------------------------------------------------------------------------- #
# folder.read cascade
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("workload_type", [AGENT, FUNCTION])
async def test_folder_read_grant_cascades_read_list_search(
    test_app,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    index_datastore_file,
    workload_type,
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    owner = DatastoreApi(authenticated_client, pod_id)
    tree = await _seed_tree(owner, index_datastore_file)

    name = f"reader_{workload_type}_{uuid4().hex[:8]}"
    workload = await create_workload(authenticated_client, pod_id, workload_type, name)
    await replace_workload_grants(
        authenticated_client,
        pod_id,
        workload_type,
        name,
        [_folder_grant(LIBRARY, "folder.read")],
    )

    client = await mint_workload_client(
        test_app,
        user_id=fixed_test_user["id"],
        workload_type=workload_type,
        workload_id=workload["id"],
        pod_id=pod_id,
        workload_name=name,
    )
    api = DatastoreApi(client, pod_id)
    try:
        # READ a file two levels below the granted folder.
        meta = await api.get_file(LEAF)
        assert meta["id"] == tree["leaf"]["id"]
        assert "folder.read" in meta["allowed_actions"]
        assert NEEDLE.encode() in await api.download_file(LEAF)

        # LIST the granted folder and its subfolder.
        lib_items = {
            i["id"] for i in (await api.list_files(directory_path=LIBRARY))["items"]
        }
        assert tree["revision"]["id"] in lib_items
        assert tree["other"]["id"] in lib_items
        rev_items = {
            i["id"] for i in (await api.list_files(directory_path=REVISION))["items"]
        }
        assert tree["leaf"]["id"] in rev_items

        # SEARCH within the granted scope reaches the descendant.
        scoped = await api.search_files(
            NEEDLE, search_method="TEXT", scope_path=LIBRARY
        )
        assert tree["leaf"]["id"] in {r["file_id"] for r in scoped["items"]}

        # READ-only grant must NOT allow writing into the folder.
        await api.upload_file(
            "intruder.md",
            b"should be denied",
            directory_path=REVISION,
            search_enabled=False,
            expected_status=status.HTTP_403_FORBIDDEN,
        )

        # No over-reach onto the ungranted sibling.
        await api.get_file(PRIVATE_FILE, expected_status=status.HTTP_403_FORBIDDEN)
        await api.list_files(
            directory_path=PRIVATE, expected_status=status.HTTP_403_FORBIDDEN
        )
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- #
# folder.write cascade (and implies read)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("workload_type", [AGENT, FUNCTION])
async def test_folder_write_grant_cascades_write_and_read(
    test_app,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    index_datastore_file,
    workload_type,
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    owner = DatastoreApi(authenticated_client, pod_id)
    tree = await _seed_tree(owner, index_datastore_file)

    name = f"writer_{workload_type}_{uuid4().hex[:8]}"
    workload = await create_workload(authenticated_client, pod_id, workload_type, name)
    await replace_workload_grants(
        authenticated_client,
        pod_id,
        workload_type,
        name,
        [_folder_grant(LIBRARY, "folder.write")],
    )

    client = await mint_workload_client(
        test_app,
        user_id=fixed_test_user["id"],
        workload_type=workload_type,
        workload_id=workload["id"],
        pod_id=pod_id,
        workload_name=name,
    )
    api = DatastoreApi(client, pod_id)
    try:
        # WRITE into a deep descendant of the granted folder.
        created = await api.upload_file(
            "by-workload.md",
            b"written via cascaded folder.write",
            directory_path=DEEP,
            search_enabled=False,
        )
        assert created["path"] == f"{DEEP}/by-workload.md"

        # folder.write implies folder.read -> the workload can also read.
        assert tree["leaf"]["id"] == (await api.get_file(LEAF))["id"]
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- #
# Search / read gated by grant — even with scope_path
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("workload_type", [AGENT, FUNCTION])
async def test_no_grant_search_and_read_are_gated(
    test_app,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    index_datastore_file,
    workload_type,
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    owner = DatastoreApi(authenticated_client, pod_id)
    tree = await _seed_tree(owner, index_datastore_file)

    name = f"nogrant_{workload_type}_{uuid4().hex[:8]}"
    workload = await create_workload(authenticated_client, pod_id, workload_type, name)
    # Intentionally NO folder grant.
    await replace_workload_grants(authenticated_client, pod_id, workload_type, name, [])

    client = await mint_workload_client(
        test_app,
        user_id=fixed_test_user["id"],
        workload_type=workload_type,
        workload_id=workload["id"],
        pod_id=pod_id,
        workload_name=name,
    )
    api = DatastoreApi(client, pod_id)
    try:
        # Scoped search is denied — passing scope_path does NOT bypass the grant.
        denied = await api.request(
            "POST",
            f"/pods/{pod_id}/datastore/files/search",
            json={
                "query": NEEDLE,
                "search_method": "TEXT",
                "scope_mode": "SUBTREE",
                "scope_path": LIBRARY,
                "limit": 10,
            },
        )
        assert denied.status_code == status.HTTP_403_FORBIDDEN, denied.text
        assert denied.json()["code"] == "MISSING_WORKLOAD_RESOURCE_GRANT"

        # Global (unscoped) search returns nothing the workload may not see.
        glob = await api.search_files(NEEDLE, search_method="TEXT")
        assert tree["leaf"]["id"] not in {r["file_id"] for r in glob["items"]}

        # Direct read/list are denied too.
        await api.get_file(LEAF, expected_status=status.HTTP_403_FORBIDDEN)
        await api.list_files(
            directory_path=LIBRARY, expected_status=status.HTTP_403_FORBIDDEN
        )
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- #
# No upward cascade: a child-folder grant must not reach a sibling
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_child_folder_grant_does_not_cascade_up(
    test_app,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    index_datastore_file,
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    owner = DatastoreApi(authenticated_client, pod_id)
    tree = await _seed_tree(owner, index_datastore_file)

    name = f"childgrant_{uuid4().hex[:8]}"
    workload = await create_workload(authenticated_client, pod_id, AGENT, name)
    # Grant only the CHILD folder /library/revision.
    await replace_workload_grants(
        authenticated_client,
        pod_id,
        AGENT,
        name,
        [_folder_grant(REVISION, "folder.read")],
    )

    client = await mint_workload_client(
        test_app,
        user_id=fixed_test_user["id"],
        workload_type=AGENT,
        workload_id=workload["id"],
        pod_id=pod_id,
        workload_name=name,
    )
    api = DatastoreApi(client, pod_id)
    try:
        # Reachable: the granted child folder and its descendants.
        assert tree["leaf"]["id"] == (await api.get_file(LEAF))["id"]
        # NOT reachable: a file directly under the PARENT (outside the grant).
        await api.get_file(LIBRARY_OTHER, expected_status=status.HTTP_403_FORBIDDEN)
        # NOT reachable: listing the parent folder itself.
        await api.list_files(
            directory_path=LIBRARY, expected_status=status.HTTP_403_FORBIDDEN
        )
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- #
# Default pod agent — user-resolved mode (no workload grant needed)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_default_pod_agent_reaches_pod_files_without_grant(
    test_app,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    index_datastore_file,
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    owner = DatastoreApi(authenticated_client, pod_id)
    tree = await _seed_tree(owner, index_datastore_file)

    client = await mint_default_pod_agent_client(
        test_app, user_id=fixed_test_user["id"], pod_id=pod_id
    )
    api = DatastoreApi(client, pod_id)
    try:
        # Mirrors the invoking user (pod owner): read/list/search all pod files
        # with NO per-resource workload grant.
        assert tree["leaf"]["id"] == (await api.get_file(LEAF))["id"]
        lib_items = {
            i["id"] for i in (await api.list_files(directory_path=LIBRARY))["items"]
        }
        assert {tree["revision"]["id"], tree["other"]["id"]} <= lib_items
        scoped = await api.search_files(
            NEEDLE, search_method="TEXT", scope_path=LIBRARY
        )
        assert tree["leaf"]["id"] in {r["file_id"] for r in scoped["items"]}
        # And, as the owner, it can also reach the "private" sibling.
        assert (await api.get_file(PRIVATE_FILE))["id"] == tree["sibling"]["id"]
    finally:
        await client.aclose()
