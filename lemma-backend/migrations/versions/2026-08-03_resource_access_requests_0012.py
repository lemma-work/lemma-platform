"""Ask for one resource instead of for the whole pod.

Until now ``pod_join_requests`` was the only way to ask for anything, and
approving one mints pod membership with a default role. That is the wrong size
of answer to "let me read this document": the sharer wanted to hand over one
file and instead hands over the pod.

``resource_access_requests`` is the right-sized ask. It mirrors the join-request
shape so the two read alike in the admin UI, but approving one writes a single
row into ``resource_permission_grants`` — keyed to the requester by user id, via
the ``USER`` grantee type — and the requester stays a non-member.

No backfill: this is a new concept with no prior representation.

Revision ID: 0012_resource_access_requests
Revises: 0011_connectors_kinds
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_resource_access_requests"
down_revision = "0011_connectors_kinds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resource_access_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "pod_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("resource_type", sa.String(length=120), nullable=False),
        # No FK: resource_id points at whichever table owns the type, exactly as
        # resource_permission_grants.resource_id does.
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_name", sa.Text(), nullable=True),
        sa.Column(
            "requester_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_permission_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "decided_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_resource_access_requests_pod_id", "resource_access_requests", ["pod_id"]
    )
    op.create_index(
        "ix_resource_access_requests_requester_user_id",
        "resource_access_requests",
        ["requester_user_id"],
    )
    op.create_index(
        "ix_resource_access_requests_decided_by_user_id",
        "resource_access_requests",
        ["decided_by_user_id"],
    )
    op.create_index(
        "ix_resource_access_requests_status", "resource_access_requests", ["status"]
    )
    # The listing an owner opens: what is still pending on this resource.
    op.create_index(
        "ix_resource_access_request_resource_status",
        "resource_access_requests",
        ["pod_id", "resource_type", "resource_id", "status"],
    )
    # "have I already asked for this?", which the guest view checks on load.
    op.create_index(
        "ix_resource_access_request_requester",
        "resource_access_requests",
        ["requester_user_id", "pod_id", "status"],
    )
    # One live ask per person per resource. Without this, a guest refreshing the
    # page or clicking twice queues duplicate requests for an owner to wade
    # through. Partial, so a rejected ask can be retried later.
    op.create_index(
        "uq_resource_access_request_pending",
        "resource_access_requests",
        ["pod_id", "resource_type", "resource_id", "requester_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_resource_access_request_pending", table_name="resource_access_requests"
    )
    op.drop_index(
        "ix_resource_access_request_requester", table_name="resource_access_requests"
    )
    op.drop_index(
        "ix_resource_access_request_resource_status",
        table_name="resource_access_requests",
    )
    op.drop_index(
        "ix_resource_access_requests_status", table_name="resource_access_requests"
    )
    op.drop_index(
        "ix_resource_access_requests_decided_by_user_id",
        table_name="resource_access_requests",
    )
    op.drop_index(
        "ix_resource_access_requests_requester_user_id",
        table_name="resource_access_requests",
    )
    op.drop_index(
        "ix_resource_access_requests_pod_id", table_name="resource_access_requests"
    )
    op.drop_table("resource_access_requests")
