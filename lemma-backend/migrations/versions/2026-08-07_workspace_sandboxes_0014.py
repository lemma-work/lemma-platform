"""Give sandboxes a table, so a workspace stops being a user id.

Until now the workspace module owned no tables at all. Workspace identity was
literally a function of the user id -- one sandbox per user,
with pods and conversations reduced to directories inside it. That made a
second workspace per user unrepresentable, and it left the durable state
describing running compute in a different database from everything else the
backend knows.

``sandboxes`` is the provisioning primitive. Both kinds live here because they
share every lifecycle column and every state transition; only the image and the
capability set differ. A workspace is owned by a user and holds a durable
volume. A function runtime is owned by a pod, runs an immutable artifact, and is
reachable only on its port -- one per pod, which is why ``owner_id`` is a pod id
there rather than a user id and carries no foreign key.

Two integers do the fencing that a reconciler used to do:

- ``epoch`` is bumped on every (re)create and is stamped into the container
  name, so an operation issued against an old epoch addresses a container that
  does not resolve. That is a definitive "gone, re-ensure" instead of a silent
  write into a replacement.
- ``storage_generation`` is bumped only when the durable disk is replaced. It
  is what lets an agent tell "your files are gone" from "this directory happens
  to be empty".

``provider_volume_id`` is adopted, never derived. The pre-consolidation volume
name is ``ab-ws-{token}`` where the token is a random uuid4 minted at row
creation in the sandbox runtime's own database, so no scheme keyed on any id in this table
can reconstruct it, so the column starts NULL and the first ensure creates one.
The backfill below sets ``id = users.id`` because workspace identity used to be
the user id itself. It is the only place that equality is load-bearing, and once a
volume is adopted the id is free to be anything.

Function sandbox rows are deliberately not backfilled. They hold no durable
state, so the resolver creates one lazily on first invocation with
``id = pod_id`` -- matching the logical id the sandbox runtime already used, so a running
function container is recognised rather than duplicated.

Revision ID: 0014_workspace_sandboxes
Revises: 0013_notifications
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0014_workspace_sandboxes"
down_revision = "0013_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sandboxes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("owner_kind", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("profile_name", sa.String(length=128), nullable=False),
        sa.Column("profile_digest", sa.String(length=71), nullable=False),
        sa.Column(
            "desired_state",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'present'"),
        ),
        sa.Column(
            "epoch", sa.BigInteger(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "storage_generation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("provider_volume_id", sa.String(length=256), nullable=True),
        sa.Column(
            "mounts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delete_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('workspace', 'function')", name="ck_sandboxes_kind"
        ),
        sa.CheckConstraint(
            "owner_kind IN ('user', 'pod')", name="ck_sandboxes_owner_kind"
        ),
        sa.CheckConstraint(
            "desired_state IN ('present', 'released', 'deleted')",
            name="ck_sandboxes_desired_state",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sandboxes"),
        sa.UniqueConstraint(
            "kind", "owner_kind", "owner_id", "slug", name="uq_sandboxes_owner_slug"
        ),
    )
    op.create_index(
        "ix_sandboxes_sweep",
        "sandboxes",
        ["desired_state", "last_used_at", "delete_after"],
    )

    op.create_table(
        "sandbox_instances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sandbox_id", sa.Uuid(), nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=256), nullable=True),
        sa.Column("provider_volume_id", sa.String(length=256), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('creating', 'ready', 'released', 'destroyed', 'error')",
            name="ck_sandbox_instances_state",
        ),
        sa.ForeignKeyConstraint(
            ["sandbox_id"],
            ["sandboxes.id"],
            name="fk_sandbox_instances_sandbox_id_sandboxes",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sandbox_instances"),
        sa.UniqueConstraint(
            "sandbox_id", "epoch", name="uq_sandbox_instances_sandbox_epoch"
        ),
    )
    op.create_index(
        "ix_sandbox_instances_live", "sandbox_instances", ["state", "provider"]
    )

    # One default workspace per existing user. `id = u.id` is what makes the
    # lazy volume adoption in the first ensure find the pre-consolidation
    # volume, whose `logical-id` label is that same user id.
    #
    # profile_name/digest are intentionally left empty and are filled by the
    # first ensure from settings. Freezing the configured digest into a
    # migration would pin every existing user to whatever image happened to be
    # deployed the day this ran.
    op.execute(
        """
        INSERT INTO sandboxes (
            id, kind, owner_kind, owner_id, slug, display_name,
            profile_name, profile_digest, desired_state,
            epoch, storage_generation, mounts, created_at, updated_at
        )
        SELECT
            u.id, 'workspace', 'user', u.id, 'default', 'Default workspace',
            '', '', 'present',
            1, 1, '[]'::jsonb, now(), now()
        FROM users u
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_sandbox_instances_live", table_name="sandbox_instances")
    op.drop_table("sandbox_instances")
    op.drop_index("ix_sandboxes_sweep", table_name="sandboxes")
    op.drop_table("sandboxes")
