"""The change-log cutover migration: up, down, and up again, and what it seeds."""

import os
import subprocess
from pathlib import Path

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0054_work_runtime"
REVISION = "0055_change_log_and_flows"


def test_record_change_log_upgrades_seeds_and_rolls_back_cleanly() -> None:
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

        def state(connection: psycopg.Connection) -> tuple[object, ...]:
            row = connection.execute(
                "SELECT to_regclass('mcp_event_deliveries'), "
                "(SELECT count(*) FROM work_items WHERE kind = 'changes.drain')"
            ).fetchone()
            assert row is not None
            return tuple(row)

        migrate("upgrade", REVISION)
        with psycopg.connect(sync_url) as connection:
            assert state(connection) == ("mcp_event_deliveries", 1)
        migrate("downgrade", BEFORE)
        with psycopg.connect(sync_url) as connection:
            assert state(connection) == (None, 0)
        migrate("upgrade", REVISION)
        with psycopg.connect(sync_url) as connection:
            assert state(connection) == ("mcp_event_deliveries", 1)
