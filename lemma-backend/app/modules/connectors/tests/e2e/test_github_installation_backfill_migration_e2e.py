"""Migration 0063 binds every GitHub installation whose owner it can derive.

Audit H4. Seeded before it: installation `solo` carried by two accounts in org
A; installation `contested` carried by accounts in org A and org B. After it:
`solo` is bound to org A, attributed to whoever connected it first, and its
accounts keep it; `contested` is bound to nobody and every account carrying it
has it cleared, so the first organization to come back through the setup
callback holds it. The downgrade drops the table.
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
BEFORE = "0062_usage_counter_basis"
REVISION = "0063_pod_connectors"
SOLO = "100000001"
CONTESTED = "100000002"


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


def _seed(plain_url: str) -> dict:
    ids: dict = {}
    with psycopg.connect(plain_url) as connection:
        run = connection.execute
        run(
            "INSERT INTO connectors (id, title, description, kinds, is_active, "
            "created_at, updated_at) VALUES ('github', 'GitHub', 'code', '[]', "
            "true, now(), now()) ON CONFLICT DO NOTHING"
        )
        for org in ("a", "b"):
            ids[org] = uuid4()
            run(
                "INSERT INTO organizations (id, name, slug, join_policy, "
                "created_at, updated_at) VALUES (%s, 'Org', %s, 'INVITE_ONLY', "
                "now(), now())",
                (ids[org], f"org-{ids[org].hex[:8]}"),
            )
            ids[f"{org}_install"] = uuid4()
            run(
                "INSERT INTO auth_configs (id, organization_id, connector_id, "
                "name, kind, config_source, status, is_default, created_at, "
                "updated_at) VALUES (%s, %s, 'github', 'github', 'http', "
                "'SYSTEM_DEFAULT', 'ACTIVE', true, now(), now())",
                (ids[f"{org}_install"], ids[org]),
            )
        # (account, org, installation, minutes ago it was connected)
        for name, org, installation, age in (
            ("first", "a", SOLO, 30),
            ("second", "a", SOLO, 10),
            ("a_contested", "a", CONTESTED, 20),
            ("b_contested", "b", CONTESTED, 5),
        ):
            ids[f"{name}_user"] = uuid4()
            run(
                "INSERT INTO users (id, email, is_verified, is_active, "
                "is_superuser, is_deleted, created_at, updated_at) VALUES "
                "(%s, %s, true, true, false, false, now(), now())",
                (ids[f"{name}_user"], f"{uuid4().hex[:8]}@example.com"),
            )
            ids[name] = uuid4()
            run(
                "INSERT INTO accounts (id, user_id, organization_id, "
                "auth_config_id, connector_id, status, is_default, external_ref, "
                "created_at, updated_at) VALUES (%s, %s, %s, %s, 'github', "
                "'CONNECTED', true, %s, now() - make_interval(mins => %s), now())",
                (
                    ids[name],
                    ids[f"{name}_user"],
                    ids[org],
                    ids[f"{org}_install"],
                    installation,
                    age,
                ),
            )
    return ids


def test_installations_are_bound_where_derivable_and_cleared_where_contested() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        ids = _seed(plain_url)

        _migrate(database_url, "upgrade", REVISION)
        with psycopg.connect(plain_url) as connection:
            bindings = connection.execute(
                "SELECT external_id, organization_id, bound_by_user_id, proven_at "
                "FROM connector_installations WHERE connector_id = 'github'"
            ).fetchall()
            refs = dict(
                connection.execute(
                    "SELECT id, external_ref FROM accounts "
                    "WHERE connector_id = 'github'"
                ).fetchall()
            )
        # Bound, but unproven: nobody showed GitHub's proof for it, so a
        # proven claim from the org that really administers it takes it over.
        assert bindings == [(SOLO, ids["a"], ids["first_user"], None)]
        assert refs == {
            ids["first"]: SOLO,
            ids["second"]: SOLO,
            ids["a_contested"]: None,
            ids["b_contested"]: None,
        }

        _migrate(database_url, "downgrade", BEFORE)
        with psycopg.connect(plain_url) as connection:
            assert connection.execute(
                "SELECT to_regclass('connector_installations')"
            ).fetchone() == (None,)
