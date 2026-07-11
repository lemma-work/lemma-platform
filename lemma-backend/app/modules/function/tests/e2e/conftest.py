"""Function module E2E fixtures."""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import status

from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e.runtime import (
    backend_server,
    configure_workspace_api_url,
    local_agentbox_server,
    workspace_image,
)

pytestmark = pytest.mark.e2e

test_network = e2e_fixtures.test_network
postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
worker = e2e_fixtures.worker
db_manager = e2e_fixtures.db_manager
test_app = e2e_fixtures.test_app
async_client = e2e_fixtures.async_client
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org
db_session = e2e_fixtures.db_session
scenario = e2e_fixtures.scenario


@pytest_asyncio.fixture
async def test_pod(authenticated_client, fixed_test_org):
    """Create a pod through the public API."""

    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Function Test Pod {uuid4()}",
            "slug": f"func-test-pod-{uuid4()}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


__all__ = [
    "authenticated_client",
    "async_client",
    "backend_server",
    "configure_workspace_api_url",
    "db_manager",
    "db_session",
    "e2e_settings",
    "fixed_test_org",
    "fixed_test_user",
    "postgres_container",
    "redis_container",
    "scenario",
    "supertokens_container",
    "test_app",
    "test_database_url",
    "test_network",
    "test_pod",
    "test_redis_url",
    "worker",
    "local_agentbox_server",
    "workspace_image",
]
