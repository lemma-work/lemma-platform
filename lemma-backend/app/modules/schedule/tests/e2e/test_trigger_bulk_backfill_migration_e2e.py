"""The trigger write-mode backfill: existing triggers fire on bulk and import writes.

A DATASTORE trigger that set neither ``bulk_writes`` nor ``import_writes`` gets
``each`` for both, and every active one is synced so its subscriber row reads
the rows of those writes. A trigger that set either keeps what it set; other
schedule types are untouched. The downgrade changes nothing.
"""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

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


def _insert(
    connection: psycopg.Connection,
    *,
    schedule_type: str,
    config: dict[str, object],
    is_active: bool = True,
) -> str:
    schedule_id = str(uuid4())
    connection.execute(
        "INSERT INTO schedules (id, user_id, pod_id, schedule_type, config,"
        " visibility, is_active, is_internal, consecutive_failures,"
        " include_outside_rows, created_at, updated_at)"
        " VALUES (%s, %s, %s, %s, %s, 'POD', %s, false, 0, false, now(), now())",
        (
            schedule_id,
            str(uuid4()),
            str(uuid4()),
            schedule_type,
            json.dumps(config),
            is_active,
        ),
    )
    return schedule_id


def _configs(connection: psycopg.Connection) -> dict[str, dict[str, object]]:
    rows = connection.execute("SELECT id::text, config FROM schedules").fetchall()
    return {str(row[0]): row[1] for row in rows}


def _syncs(connection: psycopg.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT payload->>'subscriber' FROM work_items"
        " WHERE kind = 'changes.sync_subscribers'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def test_existing_triggers_fire_on_each_bulk_and_import_write() -> None:
    scripts = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    assert scripts.get_revision(REVISION).down_revision == BEFORE

    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        environment = {**os.environ, "DATABASE_URL": database_url}
        sync_url = database_url.replace("postgresql+asyncpg", "postgresql")

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

        migrate("upgrade", BEFORE)
        watch = {"table_name": "orders", "operations": ["INSERT"]}
        with psycopg.connect(sync_url) as connection:
            # The owners and pods are beside the point; skip their foreign keys.
            connection.execute("SET session_replication_role = replica")
            old = _insert(connection, schedule_type="DATASTORE", config=watch)
            paused = _insert(
                connection, schedule_type="DATASTORE", config=watch, is_active=False
            )
            chosen = _insert(
                connection,
                schedule_type="DATASTORE",
                config={**watch, "bulk_writes": "batch"},
            )
            timed = _insert(
                connection, schedule_type="TIME", config={"cron": "0 * * * *"}
            )
            connection.execute("DELETE FROM work_items")

        migrate("upgrade", REVISION)
        each = {"bulk_writes": "each", "import_writes": "each"}
        with psycopg.connect(sync_url) as connection:
            configs = _configs(connection)
            assert configs[old] == {**watch, **each}
            assert configs[paused] == {**watch, **each}
            assert configs[chosen] == {**watch, "bulk_writes": "batch"}
            assert configs[timed] == {"cron": "0 * * * *"}
            # Every active trigger is synced -- the change-log cutover in the
            # same revision subscribes each one -- and none that is paused.
            assert _syncs(connection) == {f"schedule:{old}", f"schedule:{chosen}"}

        migrate("downgrade", BEFORE)
        with psycopg.connect(sync_url) as connection:
            assert _configs(connection)[old] == {**watch, **each}
        # Upgrading again finds nothing left to change.
        migrate("upgrade", REVISION)
        with psycopg.connect(sync_url) as connection:
            assert _configs(connection)[chosen] == {**watch, "bulk_writes": "batch"}
