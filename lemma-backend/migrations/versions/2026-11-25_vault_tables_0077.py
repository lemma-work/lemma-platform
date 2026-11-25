"""The secrets vault's tables, before anything stores a secret in them.

``vault_keys`` holds key-encryption keys, each wrapped by a root key that never
enters the database (Cloud KMS, or the configured keyset). ``vault_secrets``
holds one row per secret: a data key wrapped by a KEK, and the value sealed by
that data key with AES-256-GCM. ``vault_secret_events`` is the audit trail.

``vault_release_owned_secrets()`` is the trigger function an owner table will
use to delete the secret it points at when the owning row goes. No table uses
it yet: the next migration moves the existing secrets in and installs it.

Nothing is read or written here but the empty tables, so no key is needed to
run it. Processes create the first KEK when they start.

Revision ID: 0077_vault_tables
Revises: 0076_reports_and_blocks
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0077_vault_tables"
down_revision = "0076_reports_and_blocks"
branch_labels = None
depends_on = None

_RELEASE_FUNCTION = """
CREATE OR REPLACE FUNCTION vault_release_owned_secrets() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  col text;
  old_id uuid;
  new_id uuid;
BEGIN
  FOREACH col IN ARRAY TG_ARGV LOOP
    EXECUTE format('SELECT ($1).%I', col) USING OLD INTO old_id;
    new_id := NULL;
    IF TG_OP = 'UPDATE' THEN
      EXECUTE format('SELECT ($1).%I', col) USING NEW INTO new_id;
    END IF;
    IF old_id IS NOT NULL AND old_id IS DISTINCT FROM new_id THEN
      WITH gone AS (
        DELETE FROM vault_secrets WHERE id = old_id
        RETURNING id, organization_id, purpose, version
      )
      INSERT INTO vault_secret_events
        (id, secret_id, organization_id, purpose, action, actor_kind, version, detail, occurred_at)
      SELECT gen_random_uuid(), gone.id, gone.organization_id, gone.purpose,
             'deleted', 'owner', gone.version,
             jsonb_build_object('table', TG_TABLE_NAME, 'op', TG_OP), now()
      FROM gone;
    END IF;
  END LOOP;
  RETURN NULL;
END $$;
"""

#: Refuses a secret written under a retired (or unknown) key. The share lock
#: it takes on the key row is what makes retirement wait for in-flight writes.
_GUARD_KEY_FUNCTION = """
CREATE OR REPLACE FUNCTION vault_guard_secret_key() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM 1 FROM vault_keys
   WHERE id = NEW.kek_id AND purpose = 'encrypt' AND state <> 'retired'
   FOR KEY SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'vault key % is retired or missing', NEW.kek_id
      USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  RETURN NEW;
END $$;
"""

_GUARD_KEY_TRIGGER = (
    "CREATE TRIGGER vault_secrets_guard_key "
    "BEFORE INSERT OR UPDATE OF kek_id ON vault_secrets "
    "FOR EACH ROW EXECUTE FUNCTION vault_guard_secret_key()"
)


def _create_vault_tables() -> None:
    op.create_table(
        "vault_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("purpose", sa.String(8), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("algorithm", sa.String(16), nullable=False),
        sa.Column("root_provider", sa.String(32), nullable=False),
        sa.Column("root_key_ref", sa.Text(), nullable=False),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "purpose IN ('encrypt', 'sign')", name="ck_vault_keys_purpose"
        ),
        sa.CheckConstraint(
            "state IN ('active', 'decrypt_only', 'retired')", name="ck_vault_keys_state"
        ),
    )
    op.create_index(
        "uq_vault_keys_one_active",
        "vault_keys",
        ["purpose"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )
    op.create_table(
        "vault_secrets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid()),
        sa.Column("pod_id", sa.Uuid()),
        sa.Column("user_id", sa.Uuid()),
        sa.Column("purpose", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("owner_table", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128)),
        sa.Column("alg", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kek_id", sa.Uuid(), sa.ForeignKey("vault_keys.id"), nullable=False),
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("lease_holder", sa.String(64)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('text', 'json')", name="ck_vault_secrets_kind"),
    )
    op.create_index("ix_vault_secrets_kek_id", "vault_secrets", ["kek_id"])
    op.create_index(
        "ix_vault_secrets_organization_id",
        "vault_secrets",
        ["organization_id"],
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )
    op.create_table(
        "vault_secret_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("secret_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid()),
        sa.Column("purpose", sa.String(120)),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(160)),
        sa.Column("version", sa.Integer()),
        sa.Column("request_id", sa.String(160)),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_vault_secret_events_secret",
        "vault_secret_events",
        ["secret_id", "occurred_at"],
    )
    op.create_index(
        "ix_vault_secret_events_org",
        "vault_secret_events",
        ["organization_id", "occurred_at"],
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )
    op.execute(_RELEASE_FUNCTION)
    op.execute(_GUARD_KEY_FUNCTION)
    op.execute(_GUARD_KEY_TRIGGER)


def upgrade() -> None:
    _create_vault_tables()


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS vault_release_owned_secrets()")
    op.execute("DROP TRIGGER IF EXISTS vault_secrets_guard_key ON vault_secrets")
    op.execute("DROP FUNCTION IF EXISTS vault_guard_secret_key()")
    op.drop_table("vault_secret_events")
    op.drop_table("vault_secrets")
    op.drop_table("vault_keys")
