"""Share a resource with someone who has no account yet.

Resource grants key on a user id, which a person who has never signed in does
not have — so "Specific access" could only ever name existing pod members. The
alternative was inviting them to the organization first, which opens a far
larger door than the one being asked for.

``resource_access_invites`` holds the intended permissions against an email
address. When an account appears for that address, the identity signup event
turns each pending row into an ordinary ``USER`` grant and marks it redeemed.

Revision ID: 0013_resource_access_invites
Revises: 0012_resource_access_requests
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_resource_access_invites"
down_revision = "0012_resource_access_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resource_access_invites",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "pod_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("resource_type", sa.String(length=120), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_name", sa.Text(), nullable=True),
        # Stored normalized by the service so redemption matches on equality.
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "permission_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "status", sa.String(length=50), nullable=False, server_default="PENDING"
        ),
        sa.Column(
            "invited_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "redeemed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_resource_access_invites_pod_id", "resource_access_invites", ["pod_id"]
    )
    op.create_index(
        "ix_resource_access_invites_email", "resource_access_invites", ["email"]
    )
    op.create_index(
        "ix_resource_access_invites_status", "resource_access_invites", ["status"]
    )
    # Redemption's only query: everything owed to this address.
    op.create_index(
        "ix_resource_access_invite_email_status",
        "resource_access_invites",
        ["email", "status"],
    )
    # One live invite per address per resource, so re-inviting updates rather
    # than accumulating.
    op.create_index(
        "uq_resource_access_invite_pending",
        "resource_access_invites",
        ["pod_id", "resource_type", "resource_id", "email"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_resource_access_invite_pending", table_name="resource_access_invites"
    )
    op.drop_index(
        "ix_resource_access_invite_email_status", table_name="resource_access_invites"
    )
    op.drop_index(
        "ix_resource_access_invites_status", table_name="resource_access_invites"
    )
    op.drop_index(
        "ix_resource_access_invites_email", table_name="resource_access_invites"
    )
    op.drop_index(
        "ix_resource_access_invites_pod_id", table_name="resource_access_invites"
    )
    op.drop_table("resource_access_invites")
