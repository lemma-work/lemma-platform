"""The change-log cutover migration: up, down, and up again."""

import os
import subprocess
from pathlib import Path

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.test_utils import get_postgres_container, get_postgres_url

# Walking the Alembic chain from the base costs every one of these tests
# ten-odd seconds, so they run in the weekly protected lane. The pull-request
# lane keeps one round trip, `app/core/tests/e2e/test_work_migration_e2e.py`.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0054_work_runtime"
REVISION = "0055_change_log_and_flows"


def test_record_change_log_upgrades_and_rolls_back_cleanly() -> None:
    scripts = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    assert scripts.get_revision(REVISION).down_revision == BEFORE

    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        environment = {**os.environ, "DATABASE_URL": database_url}

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

        sync_url = database_url.replace("postgresql+asyncpg", "postgresql")

        def state(connection: psycopg.Connection) -> object:
            row = connection.execute(
                "SELECT to_regclass('mcp_event_deliveries')"
            ).fetchone()
            assert row is not None
            return row[0]

        migrate("upgrade", REVISION)
        with psycopg.connect(sync_url) as connection:
            assert state(connection) == "mcp_event_deliveries"
        migrate("downgrade", BEFORE)
        with psycopg.connect(sync_url) as connection:
            assert state(connection) is None
        migrate("upgrade", REVISION)
        with psycopg.connect(sync_url) as connection:
            assert state(connection) == "mcp_event_deliveries"
