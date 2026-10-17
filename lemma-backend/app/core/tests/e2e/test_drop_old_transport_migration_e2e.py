"""Migration 0057 drops the old transport's tables and the old columns.

The outbox is dropped whatever it still holds: the cutover replays nothing
from it. The downgrade restores the columns and the index and recreates the
tables empty, and upgrading again works.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[4]
BEFORE = "0056_agent_surface_bundle_work"
REVISION = "0057_drop_old_transport"

_TABLES = ("domain_event_outbox", "domain_event_inbox")


def _migrate(database_url: str, direction: str, revision: str) -> str:
    result = subprocess.run(
        ["uv", "run", "--no-sync", "alembic", direction, revision],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return result.stdout + result.stderr


def _state(sync_url: str) -> dict[str, object]:
    with psycopg.connect(sync_url) as connection:
        row = connection.execute(
            "SELECT to_regclass('domain_event_outbox')::text, "
            "to_regclass('domain_event_inbox')::text, "
            "to_regclass('ix_schedule_runs_recoverable')::text, "
            "(SELECT string_agg(table_name || '.' || column_name, ',' "
            "ORDER BY table_name, column_name) FROM information_schema.columns "
            "WHERE (table_name = 'agent_conversation_waits' AND column_name IN "
            "('fire_lease_until', 'wake_attempts')) OR (table_name = "
            "'schedule_runs' AND column_name = 'last_inspected_at'))"
        ).fetchone()
    assert row is not None
    return {
        "tables": tuple(name for name in row[:2] if name),
        "index": row[2],
        "columns": row[3],
    }


_OLD_COLUMNS = (
    "agent_conversation_waits.fire_lease_until,"
    "agent_conversation_waits.wake_attempts,"
    "schedule_runs.last_inspected_at"
)
_UPGRADED = {"tables": (), "index": None, "columns": None}
_KEPT_BY_0056 = {
    "tables": _TABLES,
    "index": "ix_schedule_runs_recoverable",
    "columns": _OLD_COLUMNS,
}


def test_the_old_tables_are_dropped_with_what_they_still_hold() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        sync_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        assert _state(sync_url) == _KEPT_BY_0056, "0056 is expand-only"
        with psycopg.connect(sync_url) as connection:
            connection.execute(
                "INSERT INTO domain_event_outbox (id, stream, event_type, "
                "producer, payload, occurred_at, available_at, created_at) "
                "VALUES (gen_random_uuid(), 's', 'pod.member.removed', 'p', "
                "'{}', now(), now(), now())"
            )

        _migrate(database_url, "upgrade", REVISION)
        assert _state(sync_url) == _UPGRADED

        _migrate(database_url, "downgrade", BEFORE)
        assert _state(sync_url) == _KEPT_BY_0056
        with psycopg.connect(sync_url) as connection:
            row = connection.execute(
                "SELECT (SELECT count(*) FROM domain_event_outbox), "
                "(SELECT count(*) FROM domain_event_inbox)"
            ).fetchone()
        assert row == (0, 0), "recreated empty"

        _migrate(database_url, "upgrade", REVISION)
        assert _state(sync_url) == _UPGRADED
