"""The endpoint the web onboarding calls instead of deciding this itself."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_a_new_account_is_given_a_workspace_and_a_pod(
    authenticated_client,
):
    response = await authenticated_client.post("/users/me/first-workspace")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["organization_id"]
    assert body["entry"] in {"saved", "existing", "domain_join", "new_org"}


async def test_calling_it_twice_does_not_make_a_second_workspace(
    authenticated_client,
):
    """The web onboarding runs on every load of an empty account.

    A second call has to be free, or someone who refreshed would collect
    workspaces.
    """
    first = await authenticated_client.post("/users/me/first-workspace")
    second = await authenticated_client.post("/users/me/first-workspace")
    assert first.status_code == second.status_code == 200
    assert first.json()["organization_id"] == second.json()["organization_id"], (
        "a repeat call must return the same organization"
    )
    assert second.json()["entry"] == "saved"
    assert first.json()["pod_id"] == second.json()["pod_id"]
    assert second.json()["assistant_id"]
    assert not second.json()["pod_created"]


async def test_a_caller_making_its_own_pod_is_not_given_a_spare(
    authenticated_client,
):
    """The importer lands somebody in the pod it is importing into.

    An empty pod beside it is clutter rather than a welcome, which is why the
    hook that calls this has always done the organization half only.
    """
    response = await authenticated_client.post(
        "/users/me/first-workspace", json={"with_pod": False}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["organization_id"]
    assert body["pod_id"] is None
    ready = await authenticated_client.post("/users/me/first-workspace")
    assert ready.status_code == 200, ready.text
    assert ready.json()["organization_id"] == body["organization_id"]
    assert ready.json()["pod_id"]
    assert ready.json()["assistant_id"]


async def test_concurrent_provisioning_returns_one_personal_pod(authenticated_client):
    results = await asyncio.gather(
        *[authenticated_client.post("/users/me/first-workspace") for _ in range(3)],
        return_exceptions=True,
    )
    assert all(not isinstance(result, BaseException) for result in results), results
    assert all(result.status_code == 200 for result in results)
    assert len({result.json()["pod_id"] for result in results}) == 1
    assert sum(result.json()["pod_created"] for result in results) == 1


async def test_public_endpoint_rejects_installation_organization_hints(
    authenticated_client,
):
    response = await authenticated_client.post(
        "/users/me/first-workspace",
        json={"arrived_through_organization_id": str(uuid4())},
    )
    assert response.status_code == 422


async def test_it_needs_a_session(async_client):
    response = await async_client.post("/users/me/first-workspace")
    assert response.status_code in (401, 403), response.text


async def test_the_second_person_from_a_domain_lands_in_the_first_ones_org(
    async_client, signup_user
):
    """The same rule the chat path follows, reached through HTTP."""
    from app.modules.test_support.e2e_authz import auth_headers

    domain = f"api-acme-{uuid4().hex[:8]}.com"
    first = await signup_user(email=f"ada@{domain}")
    second = await signup_user(email=f"grace@{domain}")

    one = await async_client.post(
        "/users/me/first-workspace", headers=auth_headers(first)
    )
    two = await async_client.post(
        "/users/me/first-workspace", headers=auth_headers(second)
    )
    assert one.status_code == two.status_code == 200, two.text
    assert two.json()["entry"] == "domain_join"
    assert one.json()["organization_id"] == two.json()["organization_id"]
