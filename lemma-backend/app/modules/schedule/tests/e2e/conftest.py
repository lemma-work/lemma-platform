"""Schedule module E2E fixtures."""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e.runtime import scheduler_api_server

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
db_session = e2e_fixtures.db_session
async_client = e2e_fixtures.async_client
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org
scenario = e2e_fixtures.scenario


@pytest_asyncio.fixture(scope="function", autouse=True)
async def _schedule_scheduler_api_server(scheduler_api_server):
    """Preserve the schedule e2e autouse scheduler API setup."""

    yield scheduler_api_server


@pytest_asyncio.fixture(scope="function", autouse=True)
async def redis_client(e2e_settings):
    import redis.asyncio as redis

    client = redis.from_url(e2e_settings.redis_url, decode_responses=False)
    # The production worker is session-scoped. Deleting either stream also
    # deletes its consumer groups, and FastStream permanently stops subscribers
    # that observe NOGROUP. Trim entries instead so every test starts without
    # stale events while the live worker keeps its group topology.
    for stream in ("schedule_events", "datastore.events"):
        await client.xtrim(stream, maxlen=0)
    try:
        yield client
    finally:
        for stream in ("schedule_events", "datastore.events"):
            await client.xtrim(stream, maxlen=0)
        await client.aclose()


__all__ = [
    "async_client",
    "authenticated_client",
    "db_manager",
    "db_session",
    "e2e_settings",
    "fixed_test_org",
    "fixed_test_user",
    "postgres_container",
    "redis_client",
    "redis_container",
    "scenario",
    "scheduler_api_server",
    "supertokens_container",
    "test_app",
    "test_database_url",
    "test_network",
    "test_redis_url",
    "worker",
]
