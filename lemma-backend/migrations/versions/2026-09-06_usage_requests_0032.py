"""Journal individual provider requests with exact costs and replay protection."""

import sqlalchemy as sa
from alembic import op

revision = "0030_usage_requests"
down_revision = "0022_function_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, kind in (
        ("request_id", sa.Uuid()),
        ("cost_amount", sa.Numeric(24, 9)),
        ("cached_input_tokens", sa.Integer()),
        ("cache_write_tokens", sa.Integer()),
    ):
        op.add_column("usage_records", sa.Column(name, kind, nullable=True))
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
    # Commit the column changes before the concurrent build so their table locks
    # do not block writers for the duration of the ledger scan.
    with op.get_context().autocommit_block():
        op.create_index(
            "uq_usage_request_id",
            "usage_records",
            ["request_id"],
            unique=True,
            postgresql_where=sa.text("request_id IS NOT NULL"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "uq_usage_request_id",
            table_name="usage_records",
            postgresql_concurrently=True,
        )
    op.drop_column("usage_limit_counters", "warning_emitted")
    op.drop_column("usage_limit_counters", "limit_usd")
    for name in ("used_usd", "reserved_usd"):
        op.alter_column(
            "usage_limit_counters",
            name,
            type_=sa.Float(),
            postgresql_using=f"{name}::double precision",
        )
    for name in (
        "cache_write_tokens",
        "cached_input_tokens",
        "cost_amount",
        "request_id",
    ):
        op.drop_column("usage_records", name)
