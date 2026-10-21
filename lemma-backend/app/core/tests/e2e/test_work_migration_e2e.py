"""The work runtime migration: up, down and up again, and the schema it builds is the model's.

The runtime's claim depends on exact partial-index predicates (the dedup
``ON CONFLICT`` must infer its index from them), so the migrated indexes are
compared definition by definition with what the ORM model creates.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.core.test_utils import create_postgres_database, get_postgres_url
from app.core.tests.e2e.work_support import (
    WorkDb,
)

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[4]
BEFORE = "0053_folder_visibility_index"
REVISION = "0054_work_runtime"
#: The last migration that changes the work tables: what the model matches.
MODEL_REVISION = REVISION

_TABLES = {"work_items", "work_queues", "work_rate_buckets"}
_INDEXES = """
    SELECT indexname, regexp_replace(indexdef, ' ON [a-z_.]+ ', ' ON ') AS definition
      FROM pg_indexes
     WHERE tablename IN ('work_items', 'work_queues', 'work_rate_buckets')
     ORDER BY indexname
"""
_COLUMNS = """
    SELECT table_name, column_name, data_type, is_nullable, column_default
      FROM information_schema.columns
     WHERE table_name IN ('work_items', 'work_queues', 'work_rate_buckets')
     ORDER BY table_name, column_name
"""


def _migrate(url: str, direction: str, revision: str) -> None:
    subprocess.run(
        ["uv", "run", "--no-sync", "alembic", direction, revision],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": url},
        check=True,
        capture_output=True,
    )


def _tables(sync_url: str) -> set[str]:
    with psycopg.connect(sync_url) as connection:
        rows = connection.execute(
            "SELECT tablename FROM pg_tables WHERE tablename = ANY(%s)",
            (sorted(_TABLES),),
        ).fetchall()
    return {row[0] for row in rows}


def _indexes(sync_url: str) -> list[tuple[str, str]]:
    with psycopg.connect(sync_url) as connection:
        return [(r[0], r[1]) for r in connection.execute(_INDEXES).fetchall()]


def _columns(sync_url: str) -> list[tuple[object, ...]]:
    with psycopg.connect(sync_url) as connection:
        return [tuple(r) for r in connection.execute(_COLUMNS).fetchall()]


async def test_the_migration_round_trips_and_matches_the_model(
    postgres_only_container: object,
    work_db: WorkDb,
) -> None:
    name = f"work_migration_{uuid4().hex[:8]}"
    create_postgres_database(postgres_only_container, name)
    url = get_postgres_url(postgres_only_container, name)
    sync_url = url.replace("postgresql+asyncpg", "postgresql")

    _migrate(url, "upgrade", MODEL_REVISION)
    assert _tables(sync_url) == _TABLES
    migrated = _indexes(sync_url)

    model_url = work_db.engine.url.render_as_string(hide_password=False)
    model_sync_url = model_url.replace("postgresql+asyncpg", "postgresql")
    assert migrated == _indexes(model_sync_url)
    assert _columns(sync_url) == _columns(model_sync_url)
    assert {n for n, _ in migrated} >= {
        "work_items_dedup",
        "work_items_ready",
        "work_items_running",
        "work_items_keyed",
        "work_items_leases",
        "work_queues_served",
        "work_rate_buckets_full",
    }

    _migrate(url, "downgrade", BEFORE)
    assert _tables(sync_url) == set()
    _migrate(url, "upgrade", REVISION)
    assert _tables(sync_url) == _TABLES
