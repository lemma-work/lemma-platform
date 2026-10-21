"""A test's database reset that times out on a lock names who holds it.

The reset (``truncate_all``) failing on its own ``DELETE`` says nothing about
the session that left a transaction open; ``describe_lock_holders`` is what the
``db_manager`` fixture warns with on each lock timeout it retries. Against Postgres, because the
answer comes from ``pg_stat_activity`` and ``pg_locks``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.lock_holders import (
    DatabaseResetWaited,
    describe_lock_holders,
    warn_lock_holders,
)

pytestmark = [pytest.mark.e2e]

postgres_container = e2e_fixtures.postgres_container
redis_container = e2e_fixtures.redis_container
supertokens_container = e2e_fixtures.supertokens_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
db_manager = e2e_fixtures.db_manager


async def test_a_session_left_in_a_transaction_is_named(
    db_manager, e2e_settings
) -> None:
    async with db_manager.engine.connect() as leak:
        await leak.execute(text("SET application_name = 'the-leak'"))
        await leak.execute(text("SELECT pg_advisory_xact_lock(4242)"))

        described = await describe_lock_holders(e2e_settings.database_url)

        line = next(
            (each for each in described.splitlines() if "app='the-leak'" in each),
            None,
        )
        assert line is not None, described
        # Left open, not running anything: the shape of a leaked session.
        assert "state=idle in transaction" in line
        assert "advisory:ExclusiveLock" in line
        assert "pg_advisory_xact_lock(4242)" in line

        # What a reset that waited says, in the run's warnings summary.
        with pytest.warns(DatabaseResetWaited, match="the-leak"):
            warn_lock_holders(described)
        await leak.rollback()

    assert "the-leak" not in await describe_lock_holders(e2e_settings.database_url)
