"""Vault module E2E fixtures."""

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
db_session = e2e_fixtures.db_session
