"""The group tables come and go with their migration, and only with it."""

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0041_conversation_last_activity"
AFTER = "0042_surface_groups"


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

        migrate("upgrade", BEFORE)
        assert tables() == (None, None)

        migrate("upgrade", AFTER)
        assert tables() == ("agent_surface_groups", "agent_surface_group_messages")

        migrate("downgrade", BEFORE)
        assert tables() == (None, None)

        migrate("upgrade", "head")
        assert tables() == ("agent_surface_groups", "agent_surface_group_messages")
