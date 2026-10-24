"""Machines: what a person or a pod addresses by name, built from sandboxes.

One migration for the whole machines effort. Each part of it -- machines,
the browser stream, connected machines -- is a section below with its own
``_upgrade_<part>`` / ``_downgrade_<part>`` pair and one call line in
``upgrade`` and ``downgrade``; a later part adds its section here rather
than a migration of its own.

**Machines.** `sandboxes` gains what a caller chooses about a machine and
what it is told: `template` and `size`, the `fabric` it was last provisioned
on, the `capabilities` it offers, who sees it (`visibility`), whose compute
window its time counts in (`billing_scope`), a connected machine's
`connection_id`, how many agent runs share it at once (`run_slots`) and
`created_by`. Every existing row is a default machine or a function runtime
and takes the defaults: a default machine is private, billed to its user,
and reports the size it was built at, 1x2 -- which is how the later move to
2x4 finds the rows it has not moved yet.

Five tables arrive with it:

- `machine_connections`: a person's own computer joined with the CLI;
  `connection_id` names it, without a foreign key, because a machine row
  outlives a revoked connection;
- `machine_previews`: a port reachable at a subdomain, revocable before it
  expires;
- `app_machine_consents`: a person's yes to one app running code on their
  machine;
- `machine_sessions`: a stretch of a machine running, metered in slices;
- `machine_login_backups`: encrypted copies of a browser profile's logins.

`agents.machine` is the ref an agent's commands run on, unset meaning the
default machine of whoever the run acts for.

`TABLES` is each table's DDL exactly as the models compile it, pinned to them
by ``workspace/tests/unit/test_machine_ddl_matches_models``.

Revision ID: 0064_machines
Revises: 0063_pod_connectors
"""

from alembic import op

revision = "0064_machines"
down_revision = "0063_pod_connectors"
branch_labels = None
depends_on = None

# --- machines -----------------------------------------------------------------

SANDBOX_COLUMNS = (
    "template VARCHAR(32) DEFAULT 'workspace' NOT NULL",
    "size VARCHAR(16) DEFAULT '1x2' NOT NULL",
    "fabric VARCHAR(16)",
    "capabilities JSONB DEFAULT '{}'::jsonb NOT NULL",
    "visibility VARCHAR(16) DEFAULT 'private' NOT NULL",
    "billing_scope VARCHAR(8) DEFAULT 'user' NOT NULL",
    "connection_id UUID",
    "run_slots INTEGER DEFAULT 1 NOT NULL",
    "created_by UUID",
)

SANDBOX_CHECKS = (
    (
        "CONSTRAINT ck_sandboxes_visibility "
        "CHECK (visibility IN ('private', 'pod', 'restricted'))"
    ),
    "CONSTRAINT ck_sandboxes_billing_scope CHECK (billing_scope IN ('user', 'org'))",
    "CONSTRAINT ck_sandboxes_run_slots CHECK (run_slots >= 1)",
)

AGENT_COLUMN = "machine VARCHAR(200)"

TABLES: dict[str, tuple[str, ...]] = {
    "machine_connections": (
        "CREATE TABLE machine_connections ( user_id UUID NOT NULL, organization_id UUID, pod_id UUID, name TEXT NOT NULL, display_name TEXT NOT NULL, mode TEXT NOT NULL, secret_hash TEXT NOT NULL, capabilities JSONB DEFAULT '{}'::jsonb NOT NULL, home TEXT NOT NULL, runtime_version TEXT, link_generation BIGINT DEFAULT 0 NOT NULL, last_seen_at TIMESTAMP WITH TIME ZONE, revoked_at TIMESTAMP WITH TIME ZONE, id UUID NOT NULL, created_at TIMESTAMP WITH TIME ZONE NOT NULL, updated_at TIMESTAMP WITH TIME ZONE NOT NULL, PRIMARY KEY (id), CONSTRAINT ck_machine_connections_mode CHECK (mode IN ('host', 'image')), CONSTRAINT uq_machine_connections_secret_hash UNIQUE (secret_hash), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE )",
        "CREATE UNIQUE INDEX uq_machine_connections_live_name ON machine_connections (user_id, name) WHERE revoked_at IS NULL",
    ),
    "machine_previews": (
        "CREATE TABLE machine_previews ( sandbox_id UUID NOT NULL, label VARCHAR(32) NOT NULL, port INTEGER NOT NULL, visibility VARCHAR(16) NOT NULL, created_by UUID NOT NULL, expires_at TIMESTAMP WITH TIME ZONE, revoked_at TIMESTAMP WITH TIME ZONE, id UUID NOT NULL, created_at TIMESTAMP WITH TIME ZONE NOT NULL, updated_at TIMESTAMP WITH TIME ZONE NOT NULL, PRIMARY KEY (id), CONSTRAINT ck_machine_previews_visibility CHECK (visibility IN ('private', 'link')), CONSTRAINT ck_machine_previews_port CHECK (port BETWEEN 1 AND 65535), CONSTRAINT uq_machine_previews_label UNIQUE (label), FOREIGN KEY(sandbox_id) REFERENCES sandboxes (id) ON DELETE CASCADE )",
        "CREATE INDEX ix_machine_previews_sandbox ON machine_previews (sandbox_id)",
    ),
    "app_machine_consents": (
        "CREATE TABLE app_machine_consents ( user_id UUID NOT NULL, app_id UUID NOT NULL, pod_id UUID NOT NULL, revoked_at TIMESTAMP WITH TIME ZONE, id UUID NOT NULL, created_at TIMESTAMP WITH TIME ZONE NOT NULL, updated_at TIMESTAMP WITH TIME ZONE NOT NULL, PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE )",
        "CREATE UNIQUE INDEX uq_app_machine_consents_live ON app_machine_consents (user_id, app_id) WHERE revoked_at IS NULL",
    ),
    "machine_sessions": (
        "CREATE TABLE machine_sessions ( sandbox_id UUID NOT NULL, size VARCHAR(16) NOT NULL, compute_units_per_hour FLOAT NOT NULL, billing_scope VARCHAR(8) NOT NULL, user_id UUID, organization_id UUID, pod_id UUID, started_at TIMESTAMP WITH TIME ZONE NOT NULL, metered_until TIMESTAMP WITH TIME ZONE NOT NULL, ended_at TIMESTAMP WITH TIME ZONE, id UUID NOT NULL, created_at TIMESTAMP WITH TIME ZONE NOT NULL, updated_at TIMESTAMP WITH TIME ZONE NOT NULL, PRIMARY KEY (id), CONSTRAINT ck_machine_sessions_billing_scope CHECK (billing_scope IN ('user', 'org')), FOREIGN KEY(sandbox_id) REFERENCES sandboxes (id) ON DELETE CASCADE )",
        "CREATE INDEX ix_machine_sessions_open ON machine_sessions (sandbox_id) WHERE ended_at IS NULL",
    ),
    "machine_login_backups": (
        "CREATE TABLE machine_login_backups ( sandbox_id UUID NOT NULL, object_key VARCHAR(512) NOT NULL, wrapped_key TEXT NOT NULL, size_bytes BIGINT NOT NULL, sha256 VARCHAR(64) NOT NULL, reason VARCHAR(32) NOT NULL, id UUID NOT NULL, created_at TIMESTAMP WITH TIME ZONE NOT NULL, updated_at TIMESTAMP WITH TIME ZONE NOT NULL, PRIMARY KEY (id), FOREIGN KEY(sandbox_id) REFERENCES sandboxes (id) ON DELETE CASCADE )",
        "CREATE INDEX ix_machine_login_backups_sandbox ON machine_login_backups (sandbox_id, created_at)",
    ),
}


def _upgrade_machines() -> None:
    for column in SANDBOX_COLUMNS:
        op.execute(f"ALTER TABLE sandboxes ADD COLUMN {column}")
    for check in SANDBOX_CHECKS:
        op.execute(f"ALTER TABLE sandboxes ADD {check}")
    op.execute(f"ALTER TABLE agents ADD COLUMN {AGENT_COLUMN}")
    for statements in TABLES.values():
        for statement in statements:
            op.execute(statement)


def _downgrade_machines() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute("ALTER TABLE agents DROP COLUMN machine")
    for check in (
        "ck_sandboxes_visibility",
        "ck_sandboxes_billing_scope",
        "ck_sandboxes_run_slots",
    ):
        op.execute(f"ALTER TABLE sandboxes DROP CONSTRAINT {check}")
    for column in SANDBOX_COLUMNS:
        op.execute(f"ALTER TABLE sandboxes DROP COLUMN {column.split()[0]}")


# --- every part ---------------------------------------------------------------


def upgrade() -> None:
    _upgrade_machines()


def downgrade() -> None:
    _downgrade_machines()
