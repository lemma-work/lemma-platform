"""The group tables come and go with their migration, and only with it.

They lasted until channels (``0058_channels``), which keep a group as a place of
its own channel and drop the notifications table its columns were added to; at
head both are gone.
"""

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

# Walking the Alembic chain from the base costs every one of these tests
# ten-odd seconds, so they run in the weekly protected lane. The pull-request
# lane keeps one round trip, `app/core/tests/e2e/test_work_migration_e2e.py`.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0042_mcp_access"
AFTER = "0043_surface_groups"


def test_the_group_tables_upgrade_and_roll_back_cleanly() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        environment = {**os.environ, "DATABASE_URL": database_url}
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")

        def migrate(direction: str, revision: str) -> None:
            result = subprocess.run(
                ["uv", "run", "--no-sync", "alembic", direction, revision],
                cwd=BACKEND,
                env=environment,
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert result.returncode == 0, result.stdout + result.stderr

        def tables() -> tuple[object, object]:
            with psycopg.connect(plain_url) as connection:
                row = connection.execute(
                    "SELECT to_regclass('agent_surface_groups'), "
                    "to_regclass('agent_surface_group_messages')"
                ).fetchone()
                assert row is not None
                return row[0], row[1]

        def origin_columns() -> set[str]:
            """Where a question passed on from outside the pod came from."""
            with psycopg.connect(plain_url) as connection:
                rows = connection.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'notifications' AND column_name IN "
                    "('from_outside', 'origin_group_title', 'asked_by_name')"
                ).fetchall()
            return {row[0] for row in rows}

        everything = {
            "from_outside",
            "origin_group_title",
            "asked_by_name",
        }

        migrate("upgrade", BEFORE)
        assert tables() == (None, None)
        assert origin_columns() == set()

        migrate("upgrade", AFTER)
        assert tables() == ("agent_surface_groups", "agent_surface_group_messages")
        assert origin_columns() == everything

        migrate("downgrade", BEFORE)
        assert tables() == (None, None)
        assert origin_columns() == set()

        migrate("upgrade", "head")
        assert tables() == (None, None)
        # The notifications those columns were on went with ``0058_channels``.
        assert origin_columns() == set()
