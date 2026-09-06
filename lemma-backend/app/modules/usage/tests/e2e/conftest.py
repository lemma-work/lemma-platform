"""Usage E2E fixtures."""

from app.modules.test_support.e2e import fixtures as e2e_fixtures

postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
db_manager = e2e_fixtures.db_manager

__all__ = [
    "db_manager",
    "e2e_settings",
    "postgres_container",
    "redis_container",
    "supertokens_container",
    "test_database_url",
    "test_redis_url",
]

test_app = e2e_fixtures.test_app
async_client = e2e_fixtures.async_client
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org
