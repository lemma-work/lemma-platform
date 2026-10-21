"""The workflow run times migration: up, down, and up again, keeping every instant.

The stored values were written as UTC wall time, so each must come out of the
upgrade as that instant and out of the downgrade as that wall time again --
whatever zone the database itself is set to. The database here is deliberately
set to a zone that is not UTC, which is the case a cast in the session's zone
would get wrong. And the conversion must not copy the table under its lock.
"""

import asyncio
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import asyncpg
import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.infrastructure.db.session import SESSION_ZONE
from app.core.test_utils import get_postgres_container, get_postgres_url

# Walking the Alembic chain from the base costs every one of these tests
# ten-odd seconds, so they run in the weekly protected lane. The pull-request
# lane keeps one round trip, `app/core/tests/e2e/test_work_migration_e2e.py`.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0051_agent_run_reliability"
REVISION = "0052_workflow_run_times_utc"

STARTED = datetime(2026, 3, 8, 6, 30, 15, 123456)
COMPLETED = datetime(2026, 3, 8, 7, 45, 0)


def _types(connection: psycopg.Connection) -> dict[str, str]:
    rows = connection.execute(
        "SELECT column_name, data_type FROM information_schema.columns"
        " WHERE table_name = 'workflow_flow_runs'"
        " AND column_name IN ('started_at', 'completed_at')"
    ).fetchall()
    return {str(name): str(data_type) for name, data_type in rows}


def _times(
    connection: psycopg.Connection, run_id: str
) -> tuple[datetime | None, datetime | None]:
    row = connection.execute(
        "SELECT started_at, completed_at FROM workflow_flow_runs WHERE id = %s",
        (run_id,),
    ).fetchone()
    assert row is not None
    return row[0], row[1]


def _file_node(connection: psycopg.Connection) -> int:
    row = connection.execute(
        "SELECT relfilenode FROM pg_class WHERE relname = 'workflow_flow_runs'"
    ).fetchone()
    assert row is not None
    return row[0]


def test_run_times_keep_their_instant_up_down_and_up() -> None:
    scripts = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    assert scripts.get_revision(REVISION).down_revision == BEFORE

    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        environment = {**os.environ, "DATABASE_URL": database_url}
        sync_url = database_url.replace("postgresql+asyncpg", "postgresql")

        def migrate(direction: str, revision: str) -> str:
            result = subprocess.run(
                ["uv", "run", "--no-sync", "alembic", direction, revision],
                cwd=BACKEND,
                env=environment,
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            return result.stdout + result.stderr

        with psycopg.connect(sync_url, autocommit=True) as connection:
            connection.execute(
                f"ALTER DATABASE {postgres.dbname} SET timezone = 'America/New_York'"
            )

        migrate("upgrade", BEFORE)
        finished, unfinished = str(uuid4()), str(uuid4())
        with psycopg.connect(sync_url) as connection:
            # The parent rows are beside the point; skip their foreign keys.
            connection.execute("SET session_replication_role = replica")
            for run_id, started, completed in (
                (finished, STARTED, COMPLETED),
                (unfinished, None, None),
            ):
                connection.execute(
                    "INSERT INTO workflow_flow_runs (id, flow_id, pod_id, user_id,"
                    " start_type, start_payload, status, execution_context,"
                    " execution_stack, step_history, started_at, completed_at,"
                    " created_at, updated_at) VALUES (%s, %s, %s, %s, 'MANUAL',"
                    " '{}', 'COMPLETED', '{}', '[]', '[]', %s, %s, now(), now())",
                    (run_id, uuid4(), uuid4(), uuid4(), started, completed),
                )
            assert _types(connection) == {
                "started_at": "timestamp without time zone",
                "completed_at": "timestamp without time zone",
            }
            file_node = _file_node(connection)

        def assert_aware() -> None:
            with psycopg.connect(sync_url) as connection:
                assert _types(connection) == {
                    "started_at": "timestamp with time zone",
                    "completed_at": "timestamp with time zone",
                }
                assert _times(connection, finished) == (
                    STARTED.replace(tzinfo=timezone.utc),
                    COMPLETED.replace(tzinfo=timezone.utc),
                )
                assert _times(connection, unfinished) == (None, None)
                assert _file_node(connection) == file_node, "the table was rewritten"

        # A worker on the previous release would write in New York time from
        # here on, so the operator is told to drain it first.
        output = migrate("upgrade", REVISION)
        assert "not UTC" in output and "America/New_York" in output, output
        assert_aware()

        # This release's connections are pinned to UTC, so even a naive time
        # cast the way the previous release cast it lands on its UTC instant.
        async def write_naive_as_this_release_connects() -> None:
            connection = await asyncpg.connect(
                sync_url,
                server_settings=SESSION_ZONE,
            )
            try:
                await connection.execute(
                    "UPDATE workflow_flow_runs SET started_at = $1::timestamp"
                    " WHERE id = $2::uuid",
                    STARTED,
                    finished,
                )
            finally:
                await connection.close()

        asyncio.run(write_naive_as_this_release_connects())
        assert_aware()

        migrate("downgrade", BEFORE)
        with psycopg.connect(sync_url) as connection:
            assert _types(connection) == {
                "started_at": "timestamp without time zone",
                "completed_at": "timestamp without time zone",
            }
            assert _times(connection, finished) == (STARTED, COMPLETED)
            assert _times(connection, unfinished) == (None, None)
            assert _file_node(connection) == file_node, "the table was rewritten"

        migrate("upgrade", REVISION)
        assert_aware()
