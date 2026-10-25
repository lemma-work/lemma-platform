"""Migration 0065 adds `organization_members.home_seen_at` and the index a
pod's workflow runs are read by time through.

A member already there when it runs has not looked at Home yet: the column is
empty, and the row is otherwise untouched. The downgrade drops both, and
upgrading again works.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

# Walks the Alembic chain from the base on its own Postgres: real upgrade,
# downgrade and upgrade again, which cannot be faked and costs 15-30s on a CI
# runner. Like the other chain walks it runs in the nightly protected lane.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0064_grants_every_install"
REVISION = "0065_organization_overview"
INDEX = "ix_workflow_flow_runs_pod_created"


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


def _a_member(plain_url: str) -> None:
    org, user = uuid4(), uuid4()
    with psycopg.connect(plain_url) as connection:
        run = connection.execute
        run(
            "INSERT INTO organizations (id, name, slug, join_policy, created_at, "
            "updated_at) VALUES (%s, 'Org', %s, 'INVITE_ONLY', now(), now())",
            (org, f"org-{org.hex[:8]}"),
        )
        run(
            "INSERT INTO users (id, email, is_verified, is_active, is_superuser, "
            "is_deleted, created_at, updated_at) "
            "VALUES (%s, %s, true, true, false, false, now(), now())",
            (user, f"{user.hex[:8]}@example.com"),
        )
        run(
            "INSERT INTO organization_members (id, user_id, organization_id, role, "
            "created_at, updated_at) VALUES (%s, %s, %s, 'ORG_OWNER', now(), now())",
            (uuid4(), user, org),
        )


def _column(plain_url: str) -> tuple[object, ...] | None:
    with psycopg.connect(plain_url) as connection:
        return connection.execute(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'organization_members' "
            "AND column_name = 'home_seen_at'"
        ).fetchone()


def _seen(plain_url: str) -> list[object]:
    with psycopg.connect(plain_url) as connection:
        rows = connection.execute(
            "SELECT home_seen_at FROM organization_members"
        ).fetchall()
    return [row[0] for row in rows]


def _index(plain_url: str) -> str | None:
    with psycopg.connect(plain_url) as connection:
        row = connection.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = %s", (INDEX,)
        ).fetchone()
    return row[0] if row else None


def test_members_have_not_looked_yet_and_it_rolls_back() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        assert _column(plain_url) is None and _index(plain_url) is None
        _a_member(plain_url)

        _migrate(database_url, "upgrade", REVISION)
        assert _column(plain_url) == ("timestamp with time zone", "YES")
        assert _seen(plain_url) == [None], "nobody has looked yet"
        definition = _index(plain_url)
        assert definition is not None and "(pod_id, created_at)" in definition

        _migrate(database_url, "downgrade", BEFORE)
        assert _column(plain_url) is None and _index(plain_url) is None

        _migrate(database_url, "upgrade", REVISION)
        assert _column(plain_url) == ("timestamp with time zone", "YES")
        assert _index(plain_url) is not None
