"""Regression: an AGENT workload's folder.read grant must cascade to the
POD-visible files beneath the granted folder.

Reproduces the field report ``MISSING_WORKLOAD_RESOURCE_GRANT``: a named agent
(POD toolset) holds a ``folder.read`` grant on ``/library`` yet
``pod_read_file`` / ``pod_list_files`` / ``pod_search_files`` over ``/library``
are denied at runtime even though folder grants are documented to cascade to
every descendant file and subfolder.

Unlike ``datastore/tests/e2e/test_grants_cascade.py`` (which exercises ROLE
grantees over RESTRICTED files — the human sharing path), this drives a real
AGENT delegation token over POD-visible files. That is the path the pod tools
actually take: a workload holds zero ambient access, so even a POD-visible file
needs an explicit grant, and the grant lives on the folder. It exercises
``Authorizer._authorize_delegated_workload`` /
``_matching_grant_ids_for_principal_sets`` (single-file read) and the
``sql_actions`` delegated projection (list/search) — the two places the folder
cascade must hold for workloads.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient

from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.permissions import Permissions
from app.core.authorization.service import AuthorizationDataService
from app.modules.datastore.tests.e2e.harness import DatastoreApi
from app.modules.identity.infrastructure.supertokens_auth.helpers import (
    get_user_token,
)
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)

pytestmark = pytest.mark.e2e

NEEDLE = "ZZHyponatraemiaNeedle"
LIBRARY = "/library"
REVISION = "/library/revision"
FILE_PATH = "/library/revision/hyponatraemia_algorithm.md"
SIBLING_FILE = "/private/secret.md"


async def _create_pod(authenticated_client, fixed_test_org) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"medicine-study-{uuid4().hex[:8]}",
            "description": "agent workload folder grant cascade",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _create_agent(authenticated_client, pod_id: str, name: str) -> dict:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": name, "instruction": "Answer briefly."},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _replace_agent_grants(
    authenticated_client, pod_id: str, agent_name: str, grants: list[dict]
) -> dict:
    response = await authenticated_client.put(
        f"/pods/{pod_id}/agents/{agent_name}/permissions",
        json={"grants": grants},
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()


async def _mint_agent_client(
    test_app, *, user_id: str, agent_id: str, agent_name: str, pod_id: str
) -> AsyncClient:
    """An httpx client authenticated as the agent workload, mirroring how the
    backend mints delegation tokens for agents at runtime. No delegation scope
    is set (scope=[]) — exactly like the in-process pod-tool path
    (``pod_data_access.pod_services``)."""
    claims = build_delegation_claims(
        workload_type="agent",
        workload_id=UUID(agent_id),
        pod_id=UUID(pod_id),
        session_id=uuid4().hex,
        invoked_by_user_id=UUID(user_id),
        workload_name=agent_name,
    )
    token = await get_user_token(UUID(user_id), delegation_claims=claims)
    return AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _seed_library_tree(owner: DatastoreApi, index_datastore_file) -> dict:
    """Build ``/library/revision/<file>`` plus an ungranted ``/private`` sibling.

    Files default to POD visibility (not under ``/me``), matching the reported
    pod's shared library.
    """
    library = await owner.create_folder(LIBRARY)
    assert library["path"] == LIBRARY
    assert library["visibility"] == "POD", library

    leaf = await owner.upload_file(
        "hyponatraemia_algorithm.md",
        f"{NEEDLE} severe hyponatraemia correction algorithm".encode(),
        directory_path=REVISION,
        search_enabled=True,
    )
    assert leaf["path"] == FILE_PATH
    assert leaf["visibility"] == "POD", leaf
    await index_datastore_file(UUID(leaf["pod_id"]), UUID(leaf["id"]))

    sibling = await owner.upload_file(
        "secret.md",
        b"private not-granted body",
        directory_path="/private",
        search_enabled=False,
    )
    assert sibling["path"] == SIBLING_FILE

    # The auto-created intermediate folder row must exist (mkdir -p on upload).
    revision = await owner.get_file(REVISION)
    return {"library": library, "revision": revision, "leaf": leaf, "sibling": sibling}


@pytest.mark.asyncio
async def test_agent_workload_folder_grant_cascades_to_pod_files(
    test_app,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    index_datastore_file,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    owner = DatastoreApi(authenticated_client, pod_id)
    tree = await _seed_library_tree(owner, index_datastore_file)

    agent_name = f"companion_agent_{uuid4().hex[:8]}"
    agent = await _create_agent(authenticated_client, pod_id, agent_name)

    # The canonical grant from `lemma agents grant /library:read`.
    await _replace_agent_grants(
        authenticated_client,
        pod_id,
        agent_name,
        [
            {
                "resource_type": "agent",
                "resource_name": agent_name,
                "permission_ids": ["agent.read"],
            },
            {
                "resource_type": "folder",
                "resource_name": LIBRARY,
                "permission_ids": ["folder.read"],
            },
        ],
    )

    agent_client = await _mint_agent_client(
        test_app,
        user_id=fixed_test_user["id"],
        agent_id=agent["id"],
        agent_name=agent_name,
        pod_id=pod_id,
    )
    agent_api = DatastoreApi(agent_client, pod_id)
    try:
        # --- pod_read_file: read a file two levels below the granted folder. ---
        meta = await agent_api.get_file(FILE_PATH)
        assert meta["id"] == tree["leaf"]["id"], meta
        assert "folder.read" in meta["allowed_actions"], meta
        content = await agent_api.download_file(FILE_PATH)
        assert NEEDLE.encode() in content

        # --- pod_list_files: the granted folder and its subtree are visible. ---
        lib_listing = await agent_api.list_files(directory_path=LIBRARY)
        assert tree["revision"]["id"] in {i["id"] for i in lib_listing["items"]}, (
            lib_listing
        )
        rev_listing = await agent_api.list_files(directory_path=REVISION)
        assert tree["leaf"]["id"] in {i["id"] for i in rev_listing["items"]}, (
            rev_listing
        )

        # --- pod_search_files: the descendant file is reachable under scope. ---
        results = await agent_api.search_files(
            NEEDLE, search_method="TEXT", scope_path=LIBRARY
        )
        assert tree["leaf"]["id"] in {r["file_id"] for r in results["items"]}, results

        # --- No over-reach: an ungranted sibling stays denied. ---
        await agent_api.get_file(
            SIBLING_FILE, expected_status=status.HTTP_403_FORBIDDEN
        )
    finally:
        await agent_client.aclose()


@pytest.mark.asyncio
async def test_agent_workload_folder_grant_authorizer_decision(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    index_datastore_file,
    db_session,
):
    """Pinpoints the failing layer: builds the delegated-workload context exactly
    like ``pod_data_access.pod_services`` (named agent, no delegation scope) and
    asks the authorizer to decide ``folder.read`` on the descendant file, with
    the same ResourceRef ``DatastoreAuthorization._require_document_action``
    builds (resource_type=DOCUMENT, resource_id=file id, path=file path)."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    owner = DatastoreApi(authenticated_client, pod_id)
    tree = await _seed_library_tree(owner, index_datastore_file)

    agent_name = f"companion_agent_{uuid4().hex[:8]}"
    agent = await _create_agent(authenticated_client, pod_id, agent_name)
    await _replace_agent_grants(
        authenticated_client,
        pod_id,
        agent_name,
        [
            {
                "resource_type": "folder",
                "resource_name": LIBRARY,
                "permission_ids": ["folder.read"],
            },
        ],
    )

    pod_uuid = UUID(pod_id)
    ctx = await AuthorizationDataService(db_session).build_delegated_workload_context(
        user_id=UUID(fixed_test_user["id"]),
        principal_type="AGENT",
        principal_id=UUID(agent["id"]),
        pod_id=pod_uuid,
        is_default_pod_agent=False,
        delegation_actor_name=agent_name,
    )

    file_ref = ResourceRef(
        resource_type=ResourceType.DOCUMENT,
        resource_id=UUID(tree["leaf"]["id"]),
        pod_id=pod_uuid,
        path=FILE_PATH,
    )
    decision = await ctx.authorizer.authorize(ctx, Permissions.FOLDER_READ, file_ref)
    assert decision.allowed, decision.reason_code

    # Sibling file under an ungranted folder must NOT be authorized.
    sibling_ref = ResourceRef(
        resource_type=ResourceType.DOCUMENT,
        resource_id=UUID(tree["sibling"]["id"]),
        pod_id=pod_uuid,
        path=SIBLING_FILE,
    )
    denied = await ctx.authorizer.authorize(
        ctx, Permissions.FOLDER_READ, sibling_ref
    )
    assert not denied.allowed
    assert denied.reason_code == "MISSING_WORKLOAD_RESOURCE_GRANT"


@pytest.mark.asyncio
async def test_a_grant_on_a_nested_folder_works_without_granting_its_parent(
    test_app,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    index_datastore_file,
):
    """The production failure, which the test above cannot reach.

    That one grants ``/library`` — the top of the tree — so walking the
    ancestors of a file finds every folder above it granted and search
    succeeds either way. Grant ``/library/revision`` instead and the walk climbs
    to ``/library``, which nobody granted because nobody meant to, and hides the
    file the grant was written for.

    Reading the same file by path already worked, because the single-file path
    judges the file alone. So the two disagreed: an agent could open a file it
    could not see listed. In production that was 241 of 241 files withheld from
    an agent holding a real grant on the folder containing 200 of them.
    """
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    owner = DatastoreApi(authenticated_client, pod_id)
    tree = await _seed_library_tree(owner, index_datastore_file)

    agent_name = f"nested_grant_agent_{uuid4().hex[:8]}"
    agent = await _create_agent(authenticated_client, pod_id, agent_name)

    await _replace_agent_grants(
        authenticated_client,
        pod_id,
        agent_name,
        [
            {
                "resource_type": "agent",
                "resource_name": agent_name,
                "permission_ids": ["agent.read"],
            },
            {
                # The nested folder only. `/library` above it stays ungranted.
                "resource_type": "folder",
                "resource_name": REVISION,
                "permission_ids": ["folder.read"],
            },
        ],
    )

    agent_client = await _mint_agent_client(
        test_app,
        user_id=fixed_test_user["id"],
        agent_id=agent["id"],
        agent_name=agent_name,
        pod_id=pod_id,
    )
    agent_api = DatastoreApi(agent_client, pod_id)
    try:
        # Search is the operation that broke: it filters every file in the pod
        # through `get_visible_file_ids`, which is where the ancestor walk ran.
        # That is also the call that logged `241 of 241 withheld`.
        results = await agent_api.search_files(NEEDLE, search_method="TEXT")
        assert tree["leaf"]["id"] in {r["file_id"] for r in results["items"]}, results

        # Searching and reading must agree — the disagreement was the bug.
        meta = await agent_api.get_file(FILE_PATH)
        assert meta["id"] == tree["leaf"]["id"], meta

        # And the grant must not have leaked upward or sideways: dropping the
        # ancestor walk must not turn "no grant" into access anywhere.
        assert tree["sibling"]["id"] not in {
            r["file_id"] for r in results["items"]
        }, results
        await agent_api.get_file(
            SIBLING_FILE, expected_status=status.HTTP_403_FORBIDDEN
        )
    finally:
        await agent_client.aclose()
