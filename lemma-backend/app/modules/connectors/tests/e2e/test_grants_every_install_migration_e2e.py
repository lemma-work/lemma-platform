"""Migration 0064 gives a migrated grant back every installation it reached.

Seeded at 0063, as dev was the day after the release: an org with three ACTIVE
MCP installations (Phoenix the default, PostHog, Linear) and a DISABLED one; a
pod whose `mcp` grant 0063 moved onto Phoenix only; the `posthog-mcp`
connector the runtime declared since, with no grant, which a workflow got 403
on every hour; and a second pod whose migrated connector holds no grant.

After it: `posthog-mcp` holds the grant, Linear has a PER_MEMBER connector
holding it, the disabled installation has nothing, and the grantless pod is
untouched. Run again (downgrade, upgrade), nothing changes.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

# A chain walk on its own Postgres, like the other migration tests: the nightly
# protected lane.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0063_pod_connectors"
REVISION = "0064_grants_every_install"


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


def _seed(plain_url: str) -> dict[str, UUID]:
    ids = {
        name: uuid4()
        for name in (
            "org",
            "owner",
            "pod",
            "quiet_pod",
            "phoenix",
            "posthog",
            "linear",
            "retired",
            "migrated",
            "auto",
            "quiet_migrated",
            "agent",
        )
    }
    with psycopg.connect(plain_url) as connection:
        run = connection.execute
        run(
            "INSERT INTO organizations (id, name, slug, join_policy, created_at, "
            "updated_at) VALUES (%s, 'Org', %s, 'INVITE_ONLY', now(), now())",
            (ids["org"], f"org-{ids['org'].hex[:8]}"),
        )
        run(
            "INSERT INTO users (id, email, is_verified, is_active, is_superuser, "
            "is_deleted, created_at, updated_at) VALUES "
            "(%s, %s, true, true, false, false, now(), now())",
            (ids["owner"], f"{ids['owner'].hex[:8]}@example.com"),
        )
        for pod in ("pod", "quiet_pod"):
            run(
                "INSERT INTO pods (id, user_id, organization_id, name, config, "
                "is_deleted, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, '{}', false, now(), now())",
                (ids[pod], ids["owner"], ids["org"], pod),
            )
        run(
            "INSERT INTO connectors (id, title, description, kinds, is_active, "
            "created_at, updated_at) VALUES ('mcp', 'MCP', 'mcp', '[]', true, "
            "now(), now()) ON CONFLICT DO NOTHING"
        )
        for install, name, status, default in (
            ("phoenix", "phoenix", "ACTIVE", True),
            ("posthog", "posthog-mcp", "ACTIVE", False),
            ("linear", "Linear MCP", "ACTIVE", False),
            ("retired", "retired-mcp", "DISABLED", False),
        ):
            run(
                "INSERT INTO auth_configs (id, organization_id, connector_id, "
                "name, kind, config_source, status, is_default, created_at, "
                "updated_at) VALUES (%s, %s, 'mcp', %s, 'mcp', 'SYSTEM_DEFAULT', "
                "%s, %s, now(), now())",
                (ids[install], ids["org"], name, status, default),
            )
        for connector, pod, slug, install, source in (
            ("migrated", "pod", "mcp", "phoenix", "MIGRATED"),
            ("auto", "pod", "posthog-mcp", "posthog", "AUTO"),
            ("quiet_migrated", "quiet_pod", "mcp", "phoenix", "MIGRATED"),
        ):
            run(
                "INSERT INTO pod_connectors (id, pod_id, slug, auth_config_id, "
                "mode, required, visibility, source, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, 'PER_MEMBER', false, 'POD', %s, now(), "
                "now())",
                (ids[connector], ids[pod], slug, ids[install], source),
            )
        # The migration chain does not create it; seeded as 0063's test does.
        run(
            "INSERT INTO auth_permissions (id, scope, resource_type, "
            "description, system_only, created_at, updated_at) VALUES "
            "('connector.use', 'POD', 'connector', 'x', false, now(), now()) "
            "ON CONFLICT DO NOTHING"
        )
        run(
            "INSERT INTO resource_permission_grants (id, created_at, pod_id, "
            "resource_type, resource_id, grantee_type, grantee_id, permission_id) "
            "VALUES (%s, now(), %s, 'connector', %s, 'AGENT', %s, 'connector.use')",
            (uuid4(), ids["pod"], ids["migrated"], ids["agent"]),
        )
    return ids


def _state(plain_url: str) -> set[tuple]:
    """Every PER_MEMBER connector, with the agents granted `connector.use` on it."""
    with psycopg.connect(plain_url) as connection:
        rows = connection.execute(
            "SELECT pc.pod_id, pc.slug, pc.auth_config_id, pc.source, "
            "g.grantee_id FROM pod_connectors pc "
            "LEFT JOIN resource_permission_grants g ON g.resource_id = pc.id "
            "AND g.resource_type = 'connector' "
            "AND g.permission_id = 'connector.use' "
            "WHERE pc.mode = 'PER_MEMBER'"
        ).fetchall()
    return set(rows)


def test_a_migrated_grant_reaches_every_active_installation_again() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        ids = _seed(plain_url)

        _migrate(database_url, "upgrade", REVISION)
        after = _state(plain_url)

        agent = ids["agent"]
        assert after == {
            (ids["pod"], "mcp", ids["phoenix"], "MIGRATED", agent),
            # The connector the runtime declared since, granted at last.
            (ids["pod"], "posthog-mcp", ids["posthog"], "AUTO", agent),
            # Declared here, slugged as the runtime would have.
            (ids["pod"], "linear-mcp", ids["linear"], "MIGRATED", agent),
            # No grant to carry: nothing declared, nothing granted.
            (ids["quiet_pod"], "mcp", ids["phoenix"], "MIGRATED", None),
        }

        _migrate(database_url, "downgrade", BEFORE)
        assert _state(plain_url) == after
        _migrate(database_url, "upgrade", REVISION)
        assert _state(plain_url) == after
