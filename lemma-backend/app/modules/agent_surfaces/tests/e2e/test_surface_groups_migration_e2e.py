"""The group tables come and go with their migration, and only with it."""

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = pytest.mark.e2e

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
        assert tables() == ("agent_surface_groups", "agent_surface_group_messages")
        assert origin_columns() == everything
