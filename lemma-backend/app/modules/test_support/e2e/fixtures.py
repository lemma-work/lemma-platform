"""Canonical E2E fixture exports.

Module-local ``tests/e2e/conftest.py`` files should import from here and add
only genuinely module-specific fixtures.
"""

from app.modules.test_support import e2e_base
from app.modules.test_support.e2e.builders import scenario

postgres_container = e2e_base.postgres_container
postgres_only_container = e2e_base.postgres_only_container
postgres_only_database_url = e2e_base.postgres_only_database_url
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
e2e_process_clients = e2e_base.e2e_process_clients
fixed_test_user = e2e_base.fixed_test_user
authenticated_client = e2e_base.authenticated_client
fixed_test_org = e2e_base.fixed_test_org
#: A new user and organization for every test. The module conftests bind
#: `fixed_test_user`/`fixed_test_org` to the shared module identity; a test
#: module that counts users or members, or changes the signed-in user's own
#: credentials or sessions, rebinds them to these.
fresh_test_user = e2e_base.fixed_test_user
fresh_test_org = e2e_base.fixed_test_org
module_identity = e2e_base.module_identity
module_test_user = e2e_base.module_test_user
module_test_user_own_session = e2e_base.module_test_user_own_session
module_test_org = e2e_base.module_test_org
sample_pod_entity = e2e_base.sample_pod_entity

__all__ = [
    "authenticated_client",
    "async_client",
    "db_manager",
    "db_session",
    "e2e_process_clients",
    "e2e_settings",
    "fixed_test_org",
    "fixed_test_user",
    "fresh_test_org",
    "fresh_test_user",
    "module_identity",
    "module_test_org",
    "module_test_user",
    "module_test_user_own_session",
    "postgres_container",
    "postgres_only_container",
    "postgres_only_database_url",
    "redis_container",
    "sample_pod_entity",
    "scenario",
    "supertokens_container",
    "test_app",
    "test_database_url",
    "test_redis_url",
    "sandbox_reachable_backend",
    "worker",
]
