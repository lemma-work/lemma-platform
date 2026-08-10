"""Workspace module E2E fixtures."""

from __future__ import annotations

import pytest

from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e.runtime import (
    backend_server as runtime_backend_server,
    configure_workspace_api_url as runtime_configure_workspace_api_url,
    function_image as runtime_function_image,
    local_sandbox_server as runtime_local_sandbox_server,
    workspace_image as runtime_workspace_image,
)

pytestmark = [pytest.mark.e2e, pytest.mark.workspace]

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
backend_server = runtime_backend_server
configure_workspace_api_url = runtime_configure_workspace_api_url
local_sandbox_server = runtime_local_sandbox_server
function_image = runtime_function_image
workspace_image = runtime_workspace_image
