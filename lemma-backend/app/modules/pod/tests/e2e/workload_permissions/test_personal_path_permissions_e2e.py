"""Holistic workload permissions: personal ``/me`` paths + the default-agent vs
named-agent permission model.

Two things this suite pins:

1. **Personal-path isolation.** Each user's ``/me`` (stored internally as
   ``/{user_id}``, visibility PERSONAL) is reachable only by that user. Another
   pod member — and any agent acting for a *different* user — is denied. An agent
   reaches the INVOKING user's own ``/me`` (it acts on that user's behalf) but
   never another user's.

2. **Two permission models.** The DEFAULT POD AGENT is user-equivalent within the
   pod: it inherits every pod permission the invoking user holds, so it reaches
   pod-shared resources with no grant. A USER-CREATED (named) agent starts from
   ZERO: it reaches a pod-shared resource only once explicitly granted.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import status

from app.modules.datastore.tests.e2e.harness import invite_to_pod, signup_user
from app.modules.pod.tests.e2e.workload_permissions.harness import (
    AGENT,
    DatastoreApi,
    create_agent,
    create_pod,
    mint_default_pod_agent_client,
    mint_workload_client,
    replace_workload_grants,
)

pytestmark = pytest.mark.e2e


async def _two_user_pod(authenticated_client, async_client, fixed_test_org):
    """A pod with the owner + a second pod member ``bob``."""
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    bob = await signup_user(async_client, "me-bob")
    await invite_to_pod(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        user=bob,
        role="POD_USER",
    )
    return pod_id, bob


# --------------------------------------------------------------------------- #
# /me is isolated per user
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_personal_me_is_isolated_between_users(
    authenticated_client, async_client, fixed_test_org, fixed_test_user
):
    pod_id, bob = await _two_user_pod(
        authenticated_client, async_client, fixed_test_org
    )
    owner = DatastoreApi(authenticated_client, pod_id)
    bob_api = DatastoreApi(async_client, pod_id, user=bob)
    owner_id, bob_id = fixed_test_user["id"], bob["id"]

    owner_file = await owner.upload_file(
        "owner_secret.md", b"owner private", directory_path="/me", search_enabled=False
    )
    bob_file = await bob_api.upload_file(
        "bob_secret.md", b"bob private", directory_path="/me", search_enabled=False
    )
    # The API presents each user their OWN personal space as /me; the second
    # user's id is what makes the raw path distinct (used below for cross-user
    # access). Visibility is PERSONAL.
    assert owner_file["path"] == "/me/owner_secret.md"
    assert owner_file["visibility"] == "PERSONAL"
    assert bob_file["path"] == "/me/bob_secret.md"

    # Each user reaches only their OWN /me.
    assert (await owner.get_file("/me/owner_secret.md"))["id"] == owner_file["id"]
    assert (await bob_api.get_file("/me/bob_secret.md"))["id"] == bob_file["id"]

    # Neither can reach the other's personal file via the raw /{user_id} path.
    await owner.get_file(
        f"/{bob_id}/bob_secret.md", expected_status=status.HTTP_403_FORBIDDEN
    )
    await bob_api.get_file(
        f"/{owner_id}/owner_secret.md", expected_status=status.HTTP_403_FORBIDDEN
    )

    # Owner cannot write into another user's personal space either.
    await owner.upload_file(
        "intruder.md",
        b"nope",
        directory_path=f"/{bob_id}",
        search_enabled=False,
        expected_status=status.HTTP_403_FORBIDDEN,
    )

    # Listing the root never surfaces another user's personal files.
    owner_root = {
        i["id"] for i in (await owner.list_files(directory_path="/"))["items"]
    }
    assert bob_file["id"] not in owner_root


# --------------------------------------------------------------------------- #
# Agents: reach the INVOKING user's /me, never another user's
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("agent_kind", ["named", "default"])
async def test_agents_scope_to_invoking_users_me_only(
    test_app,
    authenticated_client,
    async_client,
    fixed_test_org,
    fixed_test_user,
    agent_kind,
):
    pod_id, bob = await _two_user_pod(
        authenticated_client, async_client, fixed_test_org
    )
    owner = DatastoreApi(authenticated_client, pod_id)
    bob_api = DatastoreApi(async_client, pod_id, user=bob)
    owner_id, bob_id = fixed_test_user["id"], bob["id"]

    owner_file = await owner.upload_file(
        "owner_secret.md", b"owner private", directory_path="/me", search_enabled=False
    )
    await bob_api.upload_file(
        "bob_secret.md", b"bob private", directory_path="/me", search_enabled=False
    )

    # Build the agent client (invoked by the OWNER).
    if agent_kind == "named":
        name = f"me_agent_{uuid4().hex[:8]}"
        agent = await create_agent(authenticated_client, pod_id, name)
        # Zero grants — the named agent starts from nothing.
        await replace_workload_grants(authenticated_client, pod_id, AGENT, name, [])
        client = await mint_workload_client(
            test_app,
            user_id=owner_id,
            workload_type=AGENT,
            workload_id=agent["id"],
            pod_id=pod_id,
            workload_name=name,
        )
    else:
        client = await mint_default_pod_agent_client(
            test_app, user_id=owner_id, pod_id=pod_id
        )

    api = DatastoreApi(client, pod_id)
    try:
        # Reaches the invoking user's own /me (acts on the owner's behalf).
        assert (await api.get_file("/me/owner_secret.md"))["id"] == owner_file["id"]
        # Never reaches another user's personal space.
        await api.get_file(
            f"/{bob_id}/bob_secret.md", expected_status=status.HTTP_403_FORBIDDEN
        )
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- #
# Default agent inherits user perms; named agent starts from zero
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_default_agent_inherits_user_perms_named_agent_starts_at_zero(
    test_app, authenticated_client, fixed_test_org, fixed_test_user
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    owner = DatastoreApi(authenticated_client, pod_id)

    await owner.create_folder("/shared")
    shared = await owner.upload_file(
        "doc.md", b"pod shared body", directory_path="/shared", search_enabled=False
    )
    assert shared["visibility"] == "POD"

    # Default pod agent: no grant needed — it mirrors the owner's pod authority.
    default_client = await mint_default_pod_agent_client(
        test_app, user_id=fixed_test_user["id"], pod_id=pod_id
    )
    default_api = DatastoreApi(default_client, pod_id)
    try:
        assert (await default_api.get_file("/shared/doc.md"))["id"] == shared["id"]
    finally:
        await default_client.aclose()

    # Named agent: starts from zero -> denied until explicitly granted.
    name = f"shared_agent_{uuid4().hex[:8]}"
    agent = await create_agent(authenticated_client, pod_id, name)
    await replace_workload_grants(authenticated_client, pod_id, AGENT, name, [])
    named_client = await mint_workload_client(
        test_app,
        user_id=fixed_test_user["id"],
        workload_type=AGENT,
        workload_id=agent["id"],
        pod_id=pod_id,
        workload_name=name,
    )
    named_api = DatastoreApi(named_client, pod_id)
    try:
        await named_api.get_file(
            "/shared/doc.md", expected_status=status.HTTP_403_FORBIDDEN
        )
        # Grant folder.read on /shared -> now reachable.
        await replace_workload_grants(
            authenticated_client,
            pod_id,
            AGENT,
            name,
            [
                {
                    "resource_type": "folder",
                    "resource_name": "/shared",
                    "permission_ids": ["folder.read"],
                }
            ],
        )
        assert (await named_api.get_file("/shared/doc.md"))["id"] == shared["id"]
    finally:
        await named_client.aclose()
