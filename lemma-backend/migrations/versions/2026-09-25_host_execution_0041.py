"""Host execution: what a host can do, and which host a host sandbox runs on.

See docs/architecture/desktop-host-execution.md.

**`agent_hosts.capabilities`.** The host reports `host_execution: {enabled,
platform, available}` with its `hello` (and again in a heartbeat when the owner
flips the setting). Selection reads it to decide whether an owner's run may
execute on their Mac. JSON rather than columns because it is the host's report
verbatim, open to fields a newer host adds.

**`sandbox_host_bindings`.** A host sandbox is an ordinary `sandboxes` row whose
id marks it as one; this table says which Agent Host it runs on and how its
root is chosen (the conversation's folder, or `~/lemma/c/<day>/<slug>`), and
remembers the root the host answered with. `host_id` has no foreign key: the
host table belongs to another module, and a revoked host is answered by an op
failing, not by a binding vanishing mid-run.

Revision ID: 0041_host_execution
Revises: 0040_installation_owner
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0041_host_execution"
down_revision = "0040_installation_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_hosts",
        sa.Column(
            "capabilities",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_table(
        "sandbox_host_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "sandbox_id",
            sa.Uuid(),
            sa.ForeignKey("sandboxes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("host_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("root_hint", sa.Text(), nullable=True),
        sa.Column("root", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("sandbox_id", name="uq_sandbox_host_bindings_sandbox"),
    )


def downgrade() -> None:
    op.drop_table("sandbox_host_bindings")
    op.drop_column("agent_hosts", "capabilities")
