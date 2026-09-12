"""Give a public short link a durable home, so it lasts as long as it promises.

A `/s/{code}` link lived only as a Redis hash with a TTL. That was defensible
while the ceiling was three hours; it is not now that a link can be handed to
someone for seven days. Redis durability is a property of the deployment, not of
this code — the compose stack snapshots every 60 seconds with no append-only
file, and managed key-value services differ again — so whether a link outlived a
restart depended on how the operator had deployed, and nothing in the product
could tell the recipient which they had.

The row is the source of truth for existence, target and expiry. Redis still
serves every fetch and still owns the spend counter; see
`services/files/signed_url.py` for why that half deliberately stays lossy.

Revocation comes with the row: there was previously no way to kill a link shared
by mistake, because there was nothing to delete but a key nobody had listed.
"""

import sqlalchemy as sa
from alembic import op

revision = "0033_datastore_signed_links"
down_revision = "0030_usage_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datastore_signed_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column(
            "pod_id",
            sa.Uuid(),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column(
            "size_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("max_hits", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        # Spending the budget has to be durable for the same reason existence
        # is: the serving path rehydrates from this row when Redis has nothing,
        # so a link killed only in Redis would come back to life on the next
        # fetch after a restart — or immediately, since exhaustion drops the key.
        sa.Column("exhausted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # The code is the capability, so the lookup it serves is on the public,
    # unauthenticated path; unique because minting the same code twice would
    # hand two files the same URL.
    op.create_index(
        "ix_datastore_signed_links_code",
        "datastore_signed_links",
        ["code"],
        unique=True,
    )
    op.create_index(
        "ix_datastore_signed_link_pod_created",
        "datastore_signed_links",
        ["pod_id", "created_at"],
    )
    # Scanned by the retention sweep, which is the only reader that cares about
    # expiry without knowing a pod.
    op.create_index(
        "ix_datastore_signed_link_expires_at",
        "datastore_signed_links",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_datastore_signed_link_expires_at", "datastore_signed_links")
    op.drop_index("ix_datastore_signed_link_pod_created", "datastore_signed_links")
    op.drop_index("ix_datastore_signed_links_code", "datastore_signed_links")
    op.drop_table("datastore_signed_links")
