"""Event-transport E2E fixtures.

Redis only. These tests exercise the broker, the subscriber decorator and the
quarantine middleware against a real Redis; they never touch the database, so
pulling in postgres/supertokens would only make them slower and more fragile.
"""

from app.modules.test_support.e2e import fixtures as e2e_fixtures

redis_container = e2e_fixtures.redis_container
test_redis_url = e2e_fixtures.test_redis_url

__all__ = [
    "redis_container",
    "test_redis_url",
]
