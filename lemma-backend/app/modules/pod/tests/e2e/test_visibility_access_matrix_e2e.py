"""Who can actually open a resource at each visibility level.

The pre-existing visibility suite only flips the enum and checks that grants are
cleaned up — it never asserts an access *decision*. That is how PUBLIC shipped
unreachable: its branch in ``Authorizer.authorize`` sat after the pod-permission
gate, so a non-member was denied before it ran, and no test noticed because no
test ever asked a non-member to open anything.

This suite asks. Three viewers with genuinely different standing:

* ``member``   - in the pod, POD_VIEWER
* ``colleague`` - in the organization, NOT in the pod (the case the whole
  feature exists for: someone was sent a link at work)
* ``outsider`` - a Lemma account with no relationship to the org at all

and it pins both directions: PUBLIC widens reads to every signed-in account,
while enumeration and writes stay shut. Nothing narrower than PUBLIC reaches
past pod membership — being in the same organization buys nothing on its own.
"""

from __future__ import annotations


from uuid import uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

FILE_BODY = b"# Quarterly notes\n"


async def _make_pod(owner_client: AsyncClient, org_id: str) -> str:
    response = await owner_client.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"visibility matrix {uuid4().hex[:8]}",
            "description": "Visibility access matrix e2e pod",
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _make_file(owner_client: AsyncClient, pod_id: str) -> dict:
    filename = f"matrix_{uuid4().hex[:8]}.md"
    response = await owner_client.post(
        f"/pods/{pod_id}/datastore/files",
        data={"directory_path": "/", "visibility": "POD", "search_enabled": "false"},
        files={"data": (filename, FILE_BODY, "text/markdown")},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _set_visibility(owner_client: AsyncClient, pod_id: str, path: str, visibility: str):
    response = await owner_client.request(
        "PATCH",
        f"/pods/{pod_id}/datastore/files/by-path",
        files={"path": (None, path), "visibility": (None, visibility)},
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()


async def _read(client: AsyncClient, pod_id: str, path: str, headers: dict) -> int:
    response = await client.get(
        f"/pods/{pod_id}/datastore/files/by-path",
        params={"path": path},
        headers=headers,
    )
    return response.status_code


async def _list_tree(client: AsyncClient, pod_id: str, headers: dict) -> int:
    response = await client.get(
        f"/pods/{pod_id}/datastore/files/tree",
        params={"root_path": "/"},
        headers=headers,
    )
    return response.status_code


@pytest.fixture
async def matrix(authenticated_client: AsyncClient, async_client: AsyncClient, fixed_test_org):
    """One pod, one file, and three viewers with different standing."""
    pod_id = await _make_pod(authenticated_client, fixed_test_org["id"])
    file_payload = await _make_file(authenticated_client, pod_id)

    member = await signup_user(async_client, "matrix-member")
    colleague = await signup_user(async_client, "matrix-colleague")
    outsider = await signup_user(async_client, "matrix-outsider")

    member_org = await invite_org_member(
        authenticated_client, async_client, org_id=fixed_test_org["id"], user=member
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=member_org["id"],
        role="POD_VIEWER",
        roles=["POD_VIEWER"],
    )
    # The colleague joins the org and stops there — no pod membership. This is
    # the viewer the whole feature is about.
    await invite_org_member(
        authenticated_client, async_client, org_id=fixed_test_org["id"], user=colleague
    )
    # The outsider is never invited anywhere.

    return {
        "pod_id": pod_id,
        "path": file_payload["path"],
        "file_id": file_payload["id"],
        "member": auth_headers(member),
        "colleague": auth_headers(colleague),
        "outsider": auth_headers(outsider),
    }


class TestReadsWidenWithVisibility:
    async def test_pod_visibility_admits_only_the_member(
        self, authenticated_client: AsyncClient, async_client: AsyncClient, matrix
    ):
        pod_id, path = matrix["pod_id"], matrix["path"]

        assert await _read(async_client, pod_id, path, matrix["member"]) == status.HTTP_200_OK
        assert await _read(async_client, pod_id, path, matrix["colleague"]) != status.HTTP_200_OK
        assert await _read(async_client, pod_id, path, matrix["outsider"]) != status.HTTP_200_OK

    async def test_restricted_visibility_admits_only_granted_pod_members(
        self, authenticated_client: AsyncClient, async_client: AsyncClient, matrix
    ):
        # Being in the org buys nothing on its own: sharing reaches past the pod
        # only via PUBLIC, and everything narrower stops at pod membership.
        pod_id, path = matrix["pod_id"], matrix["path"]
        await _set_visibility(authenticated_client, pod_id, path, "RESTRICTED")

        assert await _read(async_client, pod_id, path, matrix["colleague"]) != status.HTTP_200_OK
        assert await _read(async_client, pod_id, path, matrix["outsider"]) != status.HTTP_200_OK

    async def test_public_visibility_admits_everyone_signed_in(
        self, authenticated_client: AsyncClient, async_client: AsyncClient, matrix
    ):
        pod_id, path = matrix["pod_id"], matrix["path"]
        await _set_visibility(authenticated_client, pod_id, path, "PUBLIC")

        assert await _read(async_client, pod_id, path, matrix["member"]) == status.HTTP_200_OK
        assert await _read(async_client, pod_id, path, matrix["colleague"]) == status.HTTP_200_OK
        assert await _read(async_client, pod_id, path, matrix["outsider"]) == status.HTTP_200_OK

    async def test_personal_visibility_admits_nobody_else(
        self, authenticated_client: AsyncClient, async_client: AsyncClient, matrix
    ):
        pod_id, path = matrix["pod_id"], matrix["path"]
        await _set_visibility(authenticated_client, pod_id, path, "PERSONAL")

        for viewer in ("member", "colleague", "outsider"):
            assert await _read(async_client, pod_id, path, matrix[viewer]) != status.HTTP_200_OK


class TestEnumerationStaysShut:
    async def test_non_members_cannot_walk_the_tree_even_when_they_can_read(
        self,
        authenticated_client: AsyncClient,
        async_client: AsyncClient,
        matrix,
    ):
        pod_id, path = matrix["pod_id"], matrix["path"]
        await _set_visibility(authenticated_client, pod_id, path, "PUBLIC")

        # Readable...
        assert await _read(async_client, pod_id, path, matrix["colleague"]) == status.HTTP_200_OK
        # ...but holding one link is not a directory of everything else.
        assert await _list_tree(async_client, pod_id, matrix["colleague"]) == (
            status.HTTP_403_FORBIDDEN
        )
        assert await _list_tree(async_client, pod_id, matrix["outsider"]) == (
            status.HTTP_403_FORBIDDEN
        )

    async def test_members_still_walk_the_tree(
        self, async_client: AsyncClient, matrix
    ):
        assert await _list_tree(async_client, matrix["pod_id"], matrix["member"]) == (
            status.HTTP_200_OK
        )


class TestSharePreview:
    """The endpoint the guest viewer asks before it renders anything."""

    @staticmethod
    async def _preview(client: AsyncClient, pod_id: str, path: str, headers: dict):
        return await client.get(
            f"/pods/{pod_id}/resources/document/preview",
            params={"name": path},
            headers=headers,
        )

    async def test_describes_a_resource_the_viewer_may_read(
        self, authenticated_client: AsyncClient, async_client: AsyncClient, matrix
    ):
        pod_id, path = matrix["pod_id"], matrix["path"]
        await _set_visibility(authenticated_client, pod_id, path, "PUBLIC")

        response = await self._preview(async_client, pod_id, path, matrix["colleague"])

        assert response.status_code == status.HTTP_200_OK, response.text
        body = response.json()
        assert body["visibility"] == "PUBLIC"
        assert "folder.read" in body["allowed_actions"]
        # Readable is not editable, and the preview must not imply otherwise.
        assert "folder.write" not in body["allowed_actions"]

    async def test_hides_existence_from_someone_with_no_access(
        self, authenticated_client: AsyncClient, async_client: AsyncClient, matrix
    ):
        pod_id, path = matrix["pod_id"], matrix["path"]
        await _set_visibility(authenticated_client, pod_id, path, "POD")

        response = await self._preview(async_client, pod_id, path, matrix["outsider"])

        # 404, not 403: a 403 would confirm the name exists, which is worth
        # knowing to anyone probing a pod's contents.
        assert response.status_code == status.HTTP_404_NOT_FOUND, response.text

    async def test_resolves_by_id_and_reports_the_name_back(
        self, authenticated_client: AsyncClient, async_client: AsyncClient, matrix
    ):
        # Share links carry an id, not a path, so the recipient has no name to
        # ask with — the preview has to accept the id and hand the name back.
        pod_id, path = matrix["pod_id"], matrix["path"]
        await _set_visibility(authenticated_client, pod_id, path, "PUBLIC")

        response = await async_client.get(
            f"/pods/{pod_id}/resources/document/preview",
            params={"id": matrix["file_id"]},
            headers=matrix["colleague"],
        )

        assert response.status_code == status.HTTP_200_OK, response.text
        body = response.json()
        assert body["resource_name"] == path
        assert body["resource_id"] == matrix["file_id"]

    async def test_unknown_name_is_indistinguishable_from_no_access(
        self, async_client: AsyncClient, matrix
    ):
        response = await self._preview(
            async_client, matrix["pod_id"], "/no-such-file.md", matrix["outsider"]
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND, response.text


class TestWritesDoNotWiden:
    async def test_readable_never_means_editable(
        self,
        authenticated_client: AsyncClient,
        async_client: AsyncClient,
        matrix,
    ):
        pod_id, path = matrix["pod_id"], matrix["path"]
        await _set_visibility(authenticated_client, pod_id, path, "PUBLIC")

        response = await async_client.request(
            "PATCH",
            f"/pods/{pod_id}/datastore/files/by-path",
            files={"path": (None, path), "description": (None, "edited by a stranger")},
            headers=matrix["colleague"],
        )

        assert response.status_code != status.HTTP_200_OK, response.text
