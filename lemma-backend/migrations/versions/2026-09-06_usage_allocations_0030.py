"""Keep batched accounting and exclusive budget ownership in usage."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0030_usage_allocations"
down_revision = "0022_function_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_allocations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("identity", postgresql.JSONB(), nullable=False),
        sa.Column("pricing", postgresql.JSONB(), nullable=False),
        sa.Column("counter_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("allocated", sa.Numeric(24, 9), nullable=False),
        sa.Column("remaining", sa.Numeric(24, 9), nullable=False),
        sa.Column("uncertain", sa.Numeric(24, 9), nullable=False),
        sa.Column("limited", sa.Boolean(), nullable=False),
        sa.Column("last_receipt_digest", sa.String(64), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_usage_allocations_recovery", "usage_allocations", ["state", "expires_at"]
    )
    for name, kind in (
        ("allocation_id", sa.Uuid()),
        ("batch_sequence", sa.Integer()),
        ("receipt_digest", sa.String(64)),
        ("cost_amount", sa.Numeric(24, 9)),
        ("cached_input_tokens", sa.Integer()),
        ("cache_write_tokens", sa.Integer()),
    ):
        op.add_column("usage_records", sa.Column(name, kind, nullable=True))
    op.add_column(
        "usage_records",
        sa.Column(
            "cost_source", sa.String(20), nullable=False, server_default="LEGACY"
        ),
    )
    op.create_index(
        "uq_usage_allocation_batch",
        "usage_records",
        ["allocation_id", "batch_sequence"],
        unique=True,
    )
    op.add_column(
        "usage_limit_counters", sa.Column("limit_usd", sa.Numeric(24, 9), nullable=True)
    )
    op.add_column(
        "usage_limit_counters",
        sa.Column(
            "warning_emitted", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    for name in ("used_usd", "reserved_usd"):
        op.alter_column(
            "usage_limit_counters",
            name,
            type_=sa.Numeric(24, 9),
            postgresql_using=f"ceil({name}::numeric * 1000000000) / 1000000000",
        )


def downgrade() -> None:
    op.drop_column("usage_limit_counters", "warning_emitted")
    op.drop_column("usage_limit_counters", "limit_usd")
    for name in ("used_usd", "reserved_usd"):
        op.alter_column(
            "usage_limit_counters",
            name,
            type_=sa.Float(),
            postgresql_using=f"{name}::double precision",
        )
    op.drop_index("uq_usage_allocation_batch", table_name="usage_records")
    for name in (
        "cost_source",
        "cache_write_tokens",
        "cached_input_tokens",
        "cost_amount",
        "receipt_digest",
        "batch_sequence",
        "allocation_id",
    ):
        op.drop_column("usage_records", name)
    op.drop_index("ix_usage_allocations_recovery", table_name="usage_allocations")
    op.drop_table("usage_allocations")
