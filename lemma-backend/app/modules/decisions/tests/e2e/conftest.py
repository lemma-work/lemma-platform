"""Decisions E2E fixtures: a pod, its owner, and a person outside it."""

from uuid import uuid4

import pytest_asyncio
from fastapi import status
from httpx import ASGITransport, AsyncClient

from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e_base import verify_emailpassword_for_tests

postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
db_manager = e2e_fixtures.db_manager
test_app = e2e_fixtures.test_app
async_client = e2e_fixtures.async_client
e2e_process_clients = e2e_fixtures.e2e_process_clients
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org
db_session = e2e_fixtures.db_session


async def _create_pod(client, organization_id: str) -> dict:
    response = await client.post(
        "/pods",
        json={
            "name": f"Decisions Pod {uuid4().hex[:8]}",
            "slug": f"decisions-pod-{uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": organization_id,
        },
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


@pytest_asyncio.fixture
async def test_pod(authenticated_client, fixed_test_org):
    return await _create_pod(authenticated_client, fixed_test_org["id"])


@pytest_asyncio.fixture
async def other_pod(authenticated_client, fixed_test_org):
    return await _create_pod(authenticated_client, fixed_test_org["id"])


@pytest_asyncio.fixture
async def async_client_for_stranger(test_app, fixed_test_user):
    """A second person, signed in, who is in none of the first one's pods."""
    del fixed_test_user  # the first person exists first
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://testserver"
    ) as client:
        credentials = {
            "formFields": [
                {"id": "email", "value": f"stranger+{uuid4().hex[:10]}@example.com"},
                {"id": "password", "value": "TestPassword@123"},
            ]
        }
        signed_up = (await client.post("/st/auth/signup", json=credentials)).json()
        assert signed_up.get("status") == "OK", signed_up
        await verify_emailpassword_for_tests(
            signed_up["user"]["id"], credentials["formFields"][0]["value"]
        )
        signed_in = await client.post("/st/auth/signin", json=credentials)
        token = signed_in.headers.get("st-access-token") or signed_in.cookies.get(
            "sAccessToken"
        )
        assert token
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client


__all__ = [
    "async_client_for_stranger",
    "async_client",
    "authenticated_client",
    "db_manager",
    "db_session",
    "e2e_process_clients",
    "e2e_settings",
    "fixed_test_org",
    "fixed_test_user",
    "other_pod",
    "postgres_container",
    "redis_container",
    "supertokens_container",
    "test_app",
    "test_database_url",
    "test_pod",
    "test_redis_url",
]
