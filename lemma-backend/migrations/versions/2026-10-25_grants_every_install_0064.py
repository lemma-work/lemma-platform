"""A migrated connector grant reaches every installation it reached before.

Before pod connectors, a `connector.use` grant was keyed on the connector
(`mcp`, `openapi`, ...) and covered every installation the organization had of
it. 0063 carried each one onto a PER_MEMBER pod connector on the org's
*default* installation only. A workload that called another installation then
had the runtime declare a connector for it on first use -- with no grant -- and
got `MISSING_WORKLOAD_RESOURCE_GRANT` (a PostHog MCP sync failed every hour on
a grant 0063 had moved to Phoenix).

So, per pod, for every MIGRATED PER_MEMBER connector holding grants, and every
other ACTIVE installation of the same connector in its organization: the pod's
PER_MEMBER connector for that installation -- the one the runtime declared
since, else a new one, slugged as the runtime slugs it -- gets the same grants.
Data only, and idempotent: a grant already there stays as it is.

The downgrade changes nothing. The schema is 0063's either way, and the rows
this adds are valid under it -- grants that say what the pre-0063 grant said.

Revision ID: 0064_grants_every_install
Revises: 0063_pod_connectors
"""

from __future__ import annotations

import re
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0064_grants_every_install"
down_revision = "0063_pod_connectors"
branch_labels = None
depends_on = None

#: `SLUG_PATTERN` in `connectors/domain/pod_connector.py`.
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


def _log():
    import logging

    return logging.getLogger("alembic.runtime.migration")


def _slug_base(name: str, connector_id: str) -> str:
    """`free_slug` in `pod_connector_resolution.py`, before the suffix."""
    base = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-_")[:50]
    if _SLUG.match(base):
        return base
    return re.sub(r"[^a-z0-9_-]+", "-", connector_id.lower()).strip("-_")[:50] or (
        "connector"
    )


def _free_slug(conn, pod_id, base: str) -> str:
    taken = set(
        conn.execute(
            sa.text("SELECT slug FROM pod_connectors WHERE pod_id = :pod"),
            {"pod": pod_id},
        ).scalars()
    )
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def upgrade() -> None:
    conn = op.get_bind()
    # (pod, installation) pairs a migrated grant reached and 0063 left out.
    reached = conn.execute(
        sa.text(
            "SELECT DISTINCT m.pod_id, a.id, a.name, a.connector_id "
            "FROM pod_connectors m "
            "JOIN auth_configs mac ON mac.id = m.auth_config_id "
            "JOIN pods p ON p.id = m.pod_id "
            "JOIN auth_configs a ON a.organization_id = p.organization_id "
            "AND a.connector_id = mac.connector_id AND a.status = 'ACTIVE' "
            "AND a.id <> m.auth_config_id "
            "WHERE m.source = 'MIGRATED' AND m.mode = 'PER_MEMBER' "
            "AND EXISTS (SELECT 1 FROM resource_permission_grants g "
            "WHERE g.pod_id = m.pod_id AND g.resource_type = 'connector' "
            "AND g.resource_id = m.id)"
        )
    ).all()
    declared = granted = 0
    for pod_id, auth_config_id, name, connector_id in reached:
        target = conn.execute(
            sa.text(
                "SELECT id FROM pod_connectors WHERE pod_id = :pod "
                "AND auth_config_id = :ac AND mode = 'PER_MEMBER' "
                "ORDER BY created_at LIMIT 1"
            ),
            {"pod": pod_id, "ac": auth_config_id},
        ).scalar()
        if target is None:
            target = uuid4()
            conn.execute(
                sa.text(
                    "INSERT INTO pod_connectors (id, pod_id, slug, auth_config_id, "
                    "mode, shared_pod_account_id, required, visibility, source, "
                    "created_by_user_id, created_at, updated_at) VALUES (:id, "
                    ":pod, :slug, :ac, 'PER_MEMBER', NULL, false, 'POD', "
                    "'MIGRATED', NULL, now(), now())"
                ),
                {
                    "id": target,
                    "pod": pod_id,
                    "slug": _free_slug(conn, pod_id, _slug_base(name, connector_id)),
                    "ac": auth_config_id,
                },
            )
            declared += 1
        granted += conn.execute(
            sa.text(
                "INSERT INTO resource_permission_grants (id, created_at, pod_id, "
                "resource_type, resource_id, grantee_type, grantee_id, "
                "permission_id, created_by_user_id) "
                "SELECT gen_random_uuid(), now(), g.pod_id, g.resource_type, "
                ":target, g.grantee_type, g.grantee_id, g.permission_id, "
                "g.created_by_user_id "
                "FROM resource_permission_grants g "
                "JOIN pod_connectors m ON m.id = g.resource_id "
                "JOIN auth_configs mac ON mac.id = m.auth_config_id "
                "WHERE g.pod_id = :pod AND g.resource_type = 'connector' "
                "AND m.pod_id = :pod AND m.source = 'MIGRATED' "
                "AND m.mode = 'PER_MEMBER' AND m.auth_config_id <> :ac "
                "AND mac.connector_id = :connector "
                "ON CONFLICT ON CONSTRAINT uq_resource_permission_grants_scope "
                "DO NOTHING"
            ),
            {
                "target": target,
                "pod": pod_id,
                "ac": auth_config_id,
                "connector": connector_id,
            },
        ).rowcount
    _log().info(
        "0064: %d installations reached by a migrated grant; %d pod connectors "
        "declared for them, %d grants carried.",
        len(reached),
        declared,
        granted,
    )


def downgrade() -> None:
    # Nothing to undo: see the module docstring.
    pass
