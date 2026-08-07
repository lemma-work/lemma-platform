"""Canonical E2E fixture exports.

Module-local ``tests/e2e/conftest.py`` files should import from here and add
only genuinely module-specific fixtures.
"""

from app.modules.test_support import e2e_base
from app.modules.test_support.e2e.builders import scenario

test_network = e2e_base.test_network
postgres_container = e2e_base.postgres_container
supertokens_container = e2e_base.supertokens_container
redis_container = e2e_base.redis_container
test_database_url = e2e_base.test_database_url
test_redis_url = e2e_base.test_redis_url
e2e_settings = e2e_base.e2e_settings
sandbox_reachable_backend = e2e_base.sandbox_reachable_backend
worker = e2e_base.worker
db_manager = e2e_base.db_manager
test_app = e2e_base.test_app
db_session = e2e_base.db_session
async_client = e2e_base.async_client
fixed_test_user = e2e_base.fixed_test_user
authenticated_client = e2e_base.authenticated_client
fixed_test_org = e2e_base.fixed_test_org
sample_pod_entity = e2e_base.sample_pod_entity

__all__ = [
    "authenticated_client",
    "async_client",
    "db_manager",
    "db_session",
    "e2e_settings",
    "fixed_test_org",
    "fixed_test_user",
    "postgres_container",
    "redis_container",
    "sample_pod_entity",
    "scenario",
    "supertokens_container",
    "test_app",
    "test_database_url",
    "test_network",
    "test_redis_url",
    "sandbox_reachable_backend",
    "worker",
]
