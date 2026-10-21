"""Migration 0054 goes down only while no file's bytes are keyed by its id.

Once the object layout is on, ``storage_layout`` is the only record of where a
file's bytes are. A downgrade that dropped it would leave those files reading
at path keys that no longer (or never) held them: silent loss. So it refuses,
naming the reason, and changes nothing -- and still round-trips while every
row is in the path layout.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.core.test_utils import create_postgres_database, get_postgres_url
from app.modules.test_support.e2e import fixtures as e2e_fixtures

# Walking the Alembic chain from the base costs every one of these tests
# ten-odd seconds, so they run in the weekly protected lane. The pull-request
# lane keeps one round trip, `app/core/tests/e2e/test_work_migration_e2e.py`.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

postgres_only_container = e2e_fixtures.postgres_only_container

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0053_folder_visibility_index"
REVISION = "0054_work_runtime"

_COLUMNS = (
    "SELECT count(*) FROM information_schema.columns WHERE table_name = "
    "'datastore_files' AND column_name IN "
    "('storage_layout', 'indexed_sha256', 'parent_id')"
)


def _migrate(url: str, direction: str, revision: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["uv", "run", "--no-sync", "alembic", direction, revision],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=180,
    )


def _file_row(sync_url: str, layout: str) -> None:
    with psycopg.connect(sync_url) as connection:
        # A bare row: the pod and owner it would reference are not the point.
        connection.execute("SET session_replication_role = replica")
        connection.execute(
            "INSERT INTO datastore_files (id, pod_id, owner_user_id, kind, "
            "visibility, path, name, size_bytes, search_enabled, status, "
            "created_at, updated_at, storage_layout) VALUES (%s, %s, %s, 'FILE', "
            "'POD', '/kept.pdf', 'kept.pdf', 1, false, 'NOT_REQUIRED', now(), "
            "now(), %s)",
            (uuid4(), uuid4(), uuid4(), layout),
        )


def _columns(sync_url: str) -> int:
    with psycopg.connect(sync_url) as connection:
        row = connection.execute(_COLUMNS).fetchone()
        assert row is not None
        return int(row[0])


def test_the_downgrade_refuses_while_files_are_in_the_object_layout(
    postgres_only_container: object,
) -> None:
    name = f"file_work_migration_{uuid4().hex[:8]}"
    create_postgres_database(postgres_only_container, name)
    url = get_postgres_url(postgres_only_container, name)
    sync_url = url.replace("postgresql+asyncpg", "postgresql")

    upgraded = _migrate(url, "upgrade", REVISION)
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    _file_row(sync_url, "path")
    _file_row(sync_url, "object")

    refused = _migrate(url, "downgrade", BEFORE)
    assert refused.returncode != 0
    assert "ObjectLayoutRowsExist" in refused.stderr, refused.stderr
    assert "1 datastore file(s) are stored in the object layout" in refused.stderr
    assert _columns(sync_url) == 3

    # With every file back in the path layout it is reversible, and up again.
    with psycopg.connect(sync_url) as connection:
        connection.execute(
            "DELETE FROM datastore_files WHERE storage_layout = 'object'"
        )
    downgraded = _migrate(url, "downgrade", BEFORE)
    assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr
    assert _columns(sync_url) == 0
    again = _migrate(url, "upgrade", REVISION)
    assert again.returncode == 0, again.stdout + again.stderr
    assert _columns(sync_url) == 3
