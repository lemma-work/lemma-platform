"""Migration 0066 adds a verified identity's session proof, and its downgrade
takes it away; upgrading again works.

The schema the other e2e tests use is built from the models, so this is the
one place the migration itself runs.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

# A walk of the Alembic chain on its own Postgres, like the other chain walks:
# the nightly protected lane runs it.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0065_schedule_reset_and_author"
REVISION = "0066_session_proved_identity"
_COLUMNS = ("session_proved_at", "proved_through_surface_id")


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


def _columns(sync_url: str) -> tuple[str, ...]:
    with psycopg.connect(sync_url) as connection:
        rows = connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'surface_verified_identities' "
            "AND column_name = ANY(%s) ORDER BY column_name",
            (list(_COLUMNS),),
        ).fetchall()
    return tuple(row[0] for row in rows)


def test_the_session_proof_comes_and_goes_with_the_revision() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        sync_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        assert _columns(sync_url) == ()

        _migrate(database_url, "upgrade", REVISION)
        assert _columns(sync_url) == tuple(sorted(_COLUMNS))

        _migrate(database_url, "downgrade", BEFORE)
        assert _columns(sync_url) == ()

        _migrate(database_url, "upgrade", REVISION)
        assert _columns(sync_url) == tuple(sorted(_COLUMNS))
