"""Workflow module E2E fixtures."""

from __future__ import annotations

import pytest

from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e.runtime import (
    backend_server,
    configure_workspace_api_url,
    function_image,
    full_stack,
    local_sandbox_server,
    workspace_image,
)

pytestmark = [pytest.mark.e2e, pytest.mark.workspace]

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
sample_pod_entity = e2e_fixtures.sample_pod_entity
scenario = e2e_fixtures.scenario


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
    "full_stack",
    "local_sandbox_server",
    "function_image",
    "postgres_container",
    "redis_container",
    "sample_pod_entity",
    "scenario",
    "supertokens_container",
    "test_app",
    "test_database_url",
    "test_redis_url",
    "sandbox_reachable_backend",
    "worker",
    "workspace_image",
]
