"""The deployment path for event subscriptions: up, down, and up again."""

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
BEFORE = "0046_decisions_adoption"
REVISION = "0047_mcp_event_subscriptions"
TABLES = ("mcp_event_subscriptions",)


def test_event_subscriptions_upgrade_and_roll_back_cleanly() -> None:
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
                timeout=120,
            )
            assert result.returncode == 0, result.stdout + result.stderr

        def tables(connection: psycopg.Connection) -> tuple[object, ...]:
            row = connection.execute(
                "SELECT " + ", ".join(f"to_regclass('{name}')" for name in TABLES)
            ).fetchone()
            assert row is not None
            return tuple(row)

        sync_url = database_url.replace("postgresql+asyncpg", "postgresql")
        migrate("upgrade", REVISION)
        with psycopg.connect(sync_url) as connection:
            assert tables(connection) == TABLES
        migrate("downgrade", BEFORE)
        with psycopg.connect(sync_url) as connection:
            assert tables(connection) == (None,)

        migrate("upgrade", REVISION)
        with psycopg.connect(sync_url) as connection:
            assert tables(connection) == TABLES
