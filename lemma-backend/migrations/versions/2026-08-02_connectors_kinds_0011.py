"""Collapse connector provider+kind onto a single `kind` axis.

Connectors dispatched on two axes: ``AuthProvider`` (LEMMA vs COMPOSIO) decided
which gateway ran, and a separate notion of kind decided which executor. This
revision makes ``kind`` the only discriminator. It is stored on the *install*
(``auth_configs.kind``), not the catalog row, because one connector can
legitimately be installed either way -- ``gmail``, ``google_drive``, ``slack``
and ``jira`` all ship as both a vendored package and a Composio toolkit.

Three other changes ride along because they are the same reshaping:

* **Many installs per connector.** ``ix_auth_configs_unique_active_org_app``
  allowed one active install per (org, connector), so an org could not add two
  Slack apps. It is replaced by ``uq_auth_configs_default_per_connector``, which
  permits many and pins exactly one as the default a bare ``connector_id``
  lookup resolves to.
* **Operations split in two.** ``connector_operations`` becomes catalog-only.
  Operations discovered per install (MCP tools, OpenAPI-URL endpoints) get their
  own ``auth_config_operations`` table carrying ``organization_id``, so a
  catalog query cannot return one tenant's data to another. Nothing is moved:
  the per-install column never shipped, so there are no such rows yet.
* **`connector_operations.execution`** is added here so the catalog can carry the
  polymorphic descriptor the per-kind executors consume.

Every ``LEMMA`` row becomes ``package``: at this revision the native catalog is
slack/jira/microsoft_teams/whatsapp/telegram/confluence, all vendored packages.
The http/sql/mcp kinds arrive with their executors and have no rows to migrate.

Revision ID: 0011_connectors_kinds
Revises: 0010_schedule_run_owner
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_connectors_kinds"
down_revision = "0010_schedule_run_owner"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())

# LEMMA covered every non-Composio install. At this revision that is exactly the
# vendored-package path, so the mapping is total and lossless in both directions.
_PROVIDER_TO_KIND = "CASE WHEN {col} = 'COMPOSIO' THEN 'composio' ELSE 'package' END"
_KIND_TO_PROVIDER = "CASE WHEN {col} = 'composio' THEN 'COMPOSIO' ELSE 'LEMMA' END"


def _rewrite_capability_array(column: str, from_key: str, to_key: str, expr: str) -> str:
    """Rewrite each element of a JSONB capability array, renaming its tag key."""
    return f"""
        UPDATE connectors
           SET {column} = (
               SELECT jsonb_agg(
                          (cap - '{from_key}')
                          || jsonb_build_object('{to_key}', {expr.format(col=f"cap->>'{from_key}'")})
                      )
                 FROM jsonb_array_elements({column}) AS cap
           )
         WHERE {column} IS NOT NULL
           AND jsonb_typeof({column}) = 'array'
           AND jsonb_array_length({column}) > 0
    """


def upgrade() -> None:
    # --- connectors: provider_capabilities -> kinds -------------------------
    op.execute(_rewrite_capability_array("provider_capabilities", "provider", "kind", _PROVIDER_TO_KIND))
    op.alter_column("connectors", "provider_capabilities", new_column_name="kinds")

    # --- auth_configs: provider -> kind, plus multi-install support ---------
    op.add_column("auth_configs", sa.Column("kind", sa.String(50), nullable=True))
    op.execute(f"UPDATE auth_configs SET kind = {_PROVIDER_TO_KIND.format(col='provider')}")
    op.alter_column("auth_configs", "kind", nullable=False)
    op.alter_column("auth_configs", "provider_config", new_column_name="config")
    op.add_column(
        "auth_configs",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
    )
    # The old unique index guaranteed at most one active install per
    # (org, connector), so this promotes exactly that row. row_number keeps it
    # correct even on a database where the index was already absent.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY organization_id, connector_id
                       ORDER BY created_at, id
                   ) AS rn
              FROM auth_configs
             WHERE status = 'ACTIVE'
        )
        UPDATE auth_configs a
           SET is_default = true
          FROM ranked r
         WHERE a.id = r.id AND r.rn = 1
        """
    )
    op.execute("DROP INDEX IF EXISTS ix_auth_configs_unique_active_org_app")
    op.execute("DROP INDEX IF EXISTS ix_auth_configs_app_provider_status")
    op.drop_column("auth_configs", "provider")
    op.create_index(
        "uq_auth_configs_default_per_connector",
        "auth_configs",
        ["organization_id", "connector_id"],
        unique=True,
        postgresql_where=sa.text("is_default AND status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_auth_configs_org_connector_status",
        "auth_configs",
        ["organization_id", "connector_id", "status"],
    )

    # --- connector_operations: catalog-only, keyed on kind ------------------
    op.add_column("connector_operations", sa.Column("kind", sa.String(50), nullable=True))
    op.add_column("connector_operations", sa.Column("execution", JSONB, nullable=True))
    op.execute(
        f"UPDATE connector_operations SET kind = {_PROVIDER_TO_KIND.format(col='provider')}"
    )
    op.alter_column("connector_operations", "kind", nullable=False)
    # The catalog id embeds the dispatch tag ("slack:lemma:send_message"), and
    # the importer now builds it from the kind. Rewrite existing ids to match or
    # the next import would insert duplicates alongside them. Safe because no
    # foreign key references connector_operations.id -- unlike connector_triggers,
    # whose ids schedules.connector_trigger_id points at and which are therefore
    # left exactly as they are.
    op.execute(
        """
        UPDATE connector_operations
           SET id = connector_id || ':' || kind || ':' || name
         WHERE id = connector_id || ':lemma:' || name
            OR id = connector_id || ':composio:' || name
        """
    )
    op.execute("DROP INDEX IF EXISTS ix_connector_operations_app_provider_name")
    op.execute("DROP INDEX IF EXISTS ix_connector_operations_app_provider_operation")
    op.drop_column("connector_operations", "provider")
    op.create_index(
        "uq_connector_operations_name",
        "connector_operations",
        ["connector_id", "kind", "name"],
        unique=True,
    )
    op.create_index(
        "ix_connector_operations_app_kind_operation",
        "connector_operations",
        ["connector_id", "kind", "provider_operation_name"],
    )

    # --- auth_config_operations: tenant-owned discovered operations ---------
    op.create_table(
        "auth_config_operations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "auth_config_id",
            UUID,
            sa.ForeignKey("auth_configs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            UUID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("provider_operation_name", sa.String(255), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("search_document", sa.Text(), nullable=True),
        sa.Column("input_schema", JSONB, nullable=True),
        sa.Column("output_schema", JSONB, nullable=True),
        sa.Column("execution", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_auth_config_operations_name",
        "auth_config_operations",
        ["auth_config_id", "name"],
        unique=True,
    )
    op.create_index(
        "ix_auth_config_operations_org", "auth_config_operations", ["organization_id"]
    )

    # --- connector_triggers: provider -> kind -------------------------------
    op.add_column("connector_triggers", sa.Column("kind", sa.String(50), nullable=True))
    op.execute(
        f"UPDATE connector_triggers SET kind = {_PROVIDER_TO_KIND.format(col='provider')}"
    )
    op.alter_column("connector_triggers", "kind", nullable=False)
    op.execute("DROP INDEX IF EXISTS ix_connector_triggers_app_provider_event")
    op.drop_column("connector_triggers", "provider")
    op.create_index(
        "ix_connector_triggers_app_kind_event",
        "connector_triggers",
        ["connector_id", "kind", "event_type"],
        unique=True,
    )


def downgrade() -> None:
    # --- connector_triggers -------------------------------------------------
    op.add_column(
        "connector_triggers", sa.Column("provider", sa.String(50), nullable=True)
    )
    op.execute(
        f"UPDATE connector_triggers SET provider = {_KIND_TO_PROVIDER.format(col='kind')}"
    )
    op.alter_column("connector_triggers", "provider", nullable=False)
    op.execute("DROP INDEX IF EXISTS ix_connector_triggers_app_kind_event")
    op.drop_column("connector_triggers", "kind")
    op.create_index(
        "ix_connector_triggers_app_provider_event",
        "connector_triggers",
        ["connector_id", "provider", "event_type"],
        unique=True,
    )

    # --- auth_config_operations --------------------------------------------
    # Discovered operations are cheap to re-derive (one refresh call per install)
    # and have nowhere to go in the old schema, so the table is simply dropped.
    op.drop_index("ix_auth_config_operations_org", table_name="auth_config_operations")
    op.drop_index("uq_auth_config_operations_name", table_name="auth_config_operations")
    op.drop_table("auth_config_operations")

    # --- connector_operations ----------------------------------------------
    op.add_column(
        "connector_operations", sa.Column("provider", sa.String(50), nullable=True)
    )
    op.execute(
        f"UPDATE connector_operations SET provider = {_KIND_TO_PROVIDER.format(col='kind')}"
    )
    op.alter_column("connector_operations", "provider", nullable=False)
    op.execute(
        """
        UPDATE connector_operations
           SET id = connector_id || ':' || lower(provider) || ':' || name
         WHERE id = connector_id || ':' || kind || ':' || name
        """
    )
    op.execute("DROP INDEX IF EXISTS uq_connector_operations_name")
    op.execute("DROP INDEX IF EXISTS ix_connector_operations_app_kind_operation")
    op.drop_column("connector_operations", "kind")
    op.drop_column("connector_operations", "execution")
    op.create_index(
        "ix_connector_operations_app_provider_name",
        "connector_operations",
        ["connector_id", "provider", "name"],
        unique=True,
    )
    op.create_index(
        "ix_connector_operations_app_provider_operation",
        "connector_operations",
        ["connector_id", "provider", "provider_operation_name"],
    )

    # --- auth_configs -------------------------------------------------------
    op.add_column("auth_configs", sa.Column("provider", sa.String(50), nullable=True))
    op.execute(f"UPDATE auth_configs SET provider = {_KIND_TO_PROVIDER.format(col='kind')}")
    op.execute("DROP INDEX IF EXISTS uq_auth_configs_default_per_connector")
    op.execute("DROP INDEX IF EXISTS ix_auth_configs_org_connector_status")
    op.drop_column("auth_configs", "is_default")
    op.drop_column("auth_configs", "kind")
    op.alter_column("auth_configs", "config", new_column_name="provider_config")
    # Restoring the single-install uniqueness can only succeed if no org took
    # advantage of multi-install while upgraded. Deactivate the extra installs
    # (keeping the oldest) rather than failing the downgrade or deleting rows:
    # DISABLED installs keep their accounts and can be re-enabled on re-upgrade.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY organization_id, connector_id
                       ORDER BY created_at, id
                   ) AS rn
              FROM auth_configs
             WHERE status = 'ACTIVE'
        )
        UPDATE auth_configs a
           SET status = 'DISABLED'
          FROM ranked r
         WHERE a.id = r.id AND r.rn > 1
        """
    )
    op.create_index(
        "ix_auth_configs_unique_active_org_app",
        "auth_configs",
        ["organization_id", "connector_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_auth_configs_app_provider_status",
        "auth_configs",
        ["connector_id", "provider", "status"],
    )

    # --- connectors ---------------------------------------------------------
    op.alter_column("connectors", "kinds", new_column_name="provider_capabilities")
    op.execute(
        _rewrite_capability_array("provider_capabilities", "kind", "provider", _KIND_TO_PROVIDER)
    )
