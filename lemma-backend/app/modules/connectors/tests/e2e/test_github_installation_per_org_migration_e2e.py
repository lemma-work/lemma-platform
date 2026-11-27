"""Migration 0080 lets an installation be bound to several organizations.

Up: two organizations bind the same installation, which the old constraint
refused, and one organization still cannot bind it twice. Down: the oldest
binding of each installation is kept and the rest deleted, so the old
constraint can be put back; up again works.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0079_datastore_hooks"
REVISION = "0080_github_installation_per_org"
INSTALLATION = "100000003"


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


def _org(connection) -> object:
    org_id = uuid4()
    connection.execute(
        "INSERT INTO organizations (id, name, slug, join_policy, created_at, "
        "updated_at) VALUES (%s, 'Org', %s, 'INVITE_ONLY', now(), now())",
        (org_id, f"org-{org_id.hex[:8]}"),
    )
    return org_id


def _bind(connection, org_id, *, minutes_ago: int) -> None:
    connection.execute(
        "INSERT INTO connector_installations (id, created_at, connector_id, "
        "external_id, organization_id) VALUES (gen_random_uuid(), "
        "now() - make_interval(mins => %s), 'github', %s, %s)",
        (minutes_ago, INSTALLATION, org_id),
    )


def _holders(plain_url: str) -> set:
    with psycopg.connect(plain_url) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT organization_id FROM connector_installations "
                "WHERE external_id = %s",
                (INSTALLATION,),
            ).fetchall()
        }


def test_one_installation_binds_to_several_orgs_and_down_keeps_the_oldest() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", REVISION)

        with psycopg.connect(plain_url) as connection:
            first, second = _org(connection), _org(connection)
            _bind(connection, first, minutes_ago=30)
            _bind(connection, second, minutes_ago=10)
        assert _holders(plain_url) == {first, second}

        with pytest.raises(psycopg.errors.UniqueViolation):
            with psycopg.connect(plain_url) as connection:
                _bind(connection, first, minutes_ago=1)

        _migrate(database_url, "downgrade", BEFORE)
        assert _holders(plain_url) == {first}
        with pytest.raises(psycopg.errors.UniqueViolation):
            with psycopg.connect(plain_url) as connection:
                _bind(connection, second, minutes_ago=1)

        _migrate(database_url, "upgrade", REVISION)
        with psycopg.connect(plain_url) as connection:
            _bind(connection, second, minutes_ago=1)
        assert _holders(plain_url) == {first, second}
