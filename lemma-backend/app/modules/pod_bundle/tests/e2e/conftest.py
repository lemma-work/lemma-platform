"""Pod bundle module E2E fixtures.

Re-exports the shared e2e fixtures (real ASGI app + real streaq worker
subprocess + testcontainers) the same way the function module's conftest does.
The export job runs on the real worker, so the ``worker`` fixture is required;
the ``workspace`` marker + ``configure_workspace_api_url`` autouse fixture let
the flow create a real function (its schema extraction runs in the agentbox).
"""

from __future__ import annotations

import os
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

# Point the worker subprocess's GitHub zipball fetch at the local fixture server
# (see test_import_github_e2e.py). Set at import time — BEFORE the session worker
# subprocess is spawned — so it lands in the worker's inherited environment.
GITHUB_FIXTURE_PORT = 8771
os.environ.setdefault(
    "POD_BUNDLE_GITHUB_API_BASE", f"http://127.0.0.1:{GITHUB_FIXTURE_PORT}"
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
async def workspace_api(configure_workspace_api_url):
    """Point the backend at the local agentbox so function creation works.

    Not autouse: only the function-creating roundtrip test requests it, so the
    lighter table/agent/expiry tests don't pay for the agentbox image build.
    """

    yield configure_workspace_api_url


@pytest_asyncio.fixture
async def test_pod(authenticated_client, fixed_test_org):
    """Create a pod through the public API."""

    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Bundle Test Pod {uuid4()}",
            "slug": f"bundle-test-pod-{uuid4()}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


__all__ = [
    "async_client",
    "authenticated_client",
    "backend_server",
    "configure_workspace_api_url",
    "db_manager",
    "db_session",
    "e2e_settings",
    "fixed_test_org",
    "fixed_test_user",
    "local_agentbox_server",
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
    "workspace_image",
]
