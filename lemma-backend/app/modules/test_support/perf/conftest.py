"""Fixtures for the API latency benchmark suite.

Re-exports the canonical shared E2E fixtures so the benchmark runs against the
exact same stack production uses: real FastAPI app (factory), real Postgres
(testcontainer, with production indexes via create_all), SuperTokens auth, and
the httpx ASGI client. The ASGI transport means measurements EXCLUDE real
network latency and production DB hardware -- which is precisely the point: it
isolates *application + DB* time from infra so we can tell whether the prod 13s
is the code or the cluster.
"""

from __future__ import annotations

import pytest

from app.modules.test_support.e2e import fixtures as e2e_fixtures

pytestmark = pytest.mark.e2e

postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
db_manager = e2e_fixtures.db_manager
test_app = e2e_fixtures.test_app
db_session = e2e_fixtures.db_session
async_client = e2e_fixtures.async_client
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org
