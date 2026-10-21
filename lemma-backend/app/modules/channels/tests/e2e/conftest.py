"""Channels module E2E fixtures."""

from __future__ import annotations

from app.modules.channels.tests.e2e import channel_pod
from app.modules.test_support.e2e import fixtures as e2e_fixtures

# Re-export shared E2E fixtures so this module can run with --confcutdir.
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
scenario = e2e_fixtures.scenario
pod = channel_pod.pod
