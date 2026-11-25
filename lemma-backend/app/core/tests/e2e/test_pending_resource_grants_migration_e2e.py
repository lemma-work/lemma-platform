"""Migration 0077 adds `pending_resource_grants`, and takes it away again.

It only adds a table, so the test is that the table appears, goes on the
downgrade, and comes back on a second upgrade.
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
BEFORE = "0076_reports_and_blocks"
REVISION = "0077_pending_resource_grants"


def _migrate(database_url: str, direction: str, revision: str) -> None:
    result = subprocess.run(
        ["uv", "run", "--no-sync", "alembic", direction, revision],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]


def _has_table(plain_url: str) -> bool:
    with psycopg.connect(plain_url) as connection:
        row = connection.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'pending_resource_grants'"
        ).fetchone()
    return row is not None


def test_the_table_comes_and_goes_with_the_revision() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        assert not _has_table(plain_url)

        _migrate(database_url, "upgrade", REVISION)
        assert _has_table(plain_url)

        _migrate(database_url, "downgrade", BEFORE)
        assert not _has_table(plain_url)

        _migrate(database_url, "upgrade", REVISION)
        assert _has_table(plain_url)
