"""The deployment path for the MCP access tables: up, down, and up again."""

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
BEFORE = "0041_conversation_last_activity"
REVISION = "0042_mcp_access"
TABLES = ("mcp_oauth_clients", "mcp_oauth_grants", "mcp_oauth_tokens")


def test_mcp_access_tables_upgrade_and_roll_back_cleanly() -> None:
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
            live_index = connection.execute(
                "SELECT i.indisunique, pg_get_expr(i.indpred, i.indrelid) "
                "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = 'uq_mcp_oauth_grants_live'"
            ).fetchone()
            assert live_index == (True, "(revoked_at IS NULL)")

        migrate("downgrade", BEFORE)
        with psycopg.connect(sync_url) as connection:
            assert tables(connection) == (None, None, None)

        migrate("upgrade", REVISION)
        with psycopg.connect(sync_url) as connection:
            assert tables(connection) == TABLES
