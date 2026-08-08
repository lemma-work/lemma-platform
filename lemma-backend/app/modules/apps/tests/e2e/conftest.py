from __future__ import annotations

from uuid import uuid4

import pytest_asyncio
from fastapi import status

from app.modules.test_support.e2e import fixtures as e2e_fixtures

test_network = e2e_fixtures.test_network
postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
sandbox_reachable_backend = e2e_fixtures.sandbox_reachable_backend
worker = e2e_fixtures.worker
db_manager = e2e_fixtures.db_manager
test_app = e2e_fixtures.test_app
db_session = e2e_fixtures.db_session
async_client = e2e_fixtures.async_client
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org
scenario = e2e_fixtures.scenario


@pytest_asyncio.fixture
async def test_pod(authenticated_client, fixed_test_org):
    payload = {
        "name": f"App Test Pod {uuid4()}",
        "slug": f"app-test-pod-{uuid4()}",
        "type": "ASSISTANT",
        "organization_id": fixed_test_org["id"],
    }
    response = await authenticated_client.post(
        "/pods",
        json=payload,
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()
