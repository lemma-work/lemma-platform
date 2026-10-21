"""Migration 0060 creates the Slack tables with their constraints, and its
downgrade drops them; upgrading again works.

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

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0059_drop_old_transport"
REVISION = "0060_slack_apps"

_TABLES = (
    "slack_configuration_tokens",
    "slack_manager_installs",
    "slack_workspace_people",
    "slack_pending_app_bindings",
)


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


def _tables(sync_url: str) -> tuple[str, ...]:
    with psycopg.connect(sync_url) as connection:
        row = connection.execute(
            "SELECT " + ", ".join(f"to_regclass('{name}')::text" for name in _TABLES)
        ).fetchone()
    assert row is not None
    return tuple(name for name in row if name)


def _constraints(sync_url: str) -> dict[str, str]:
    """Each constraint on the new tables, by name, as Postgres states it."""
    with psycopg.connect(sync_url) as connection:
        rows = connection.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid::regclass::text = ANY(%s)",
            (list(_TABLES),),
        ).fetchall()
    return dict(rows)


def test_the_slack_tables_come_and_go_with_the_revision() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        sync_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        assert _tables(sync_url) == ()

        _migrate(database_url, "upgrade", REVISION)
        assert _tables(sync_url) == _TABLES
        constraints = _constraints(sync_url)
        assert constraints["uq_slack_configuration_token_org_team"] == (
            "UNIQUE (organization_id, team_id)"
        )
        assert constraints["fk_slack_pending_app_binding_pod_org"] == (
            "FOREIGN KEY (pod_id, organization_id) REFERENCES "
            "pods(id, organization_id) ON UPDATE CASCADE ON DELETE CASCADE"
        )
        proved_by = constraints["ck_slack_workspace_person_proved_by"]
        assert "'INSTALL'" in proved_by and "'OIDC'" in proved_by
        assert "'LINK'" not in proved_by
        assert "uq_slack_manager_installs_account" in constraints

        with psycopg.connect(sync_url) as connection:
            org = connection.execute(
                "INSERT INTO organizations (id, name, slug, join_policy, created_at, "
                "updated_at) VALUES (gen_random_uuid(), 'Acme', 'acme-slack-0060', "
                "'INVITE_ONLY', now(), now()) "
                "RETURNING id"
            ).fetchone()
            assert org is not None
            connection.execute(
                "INSERT INTO slack_configuration_tokens (id, organization_id, "
                "team_id, access_token, refresh_token, access_expires_at, "
                "saved_at, created_at, updated_at) VALUES (gen_random_uuid(), "
                "%s, 'T1', 'a', 'r', now(), now(), now(), now())",
                (org[0],),
            )
            generation = connection.execute(
                "SELECT generation FROM slack_configuration_tokens"
            ).fetchone()
            connection.execute("DELETE FROM organizations WHERE id = %s", (org[0],))
            left = connection.execute(
                "SELECT count(*) FROM slack_configuration_tokens"
            ).fetchone()
        assert generation == (0,)
        assert left == (0,), "an organization's tokens go with it"

        _migrate(database_url, "downgrade", BEFORE)
        assert _tables(sync_url) == ()

        _migrate(database_url, "upgrade", REVISION)
        assert _tables(sync_url) == _TABLES
