"""Add identity verification and deactivation state.

Revision ID: 0007_auth_hardening
Revises: 0006_conversation_history_index
"""

import sqlalchemy as sa
from alembic import op


revision = "0007_auth_hardening"
down_revision = "0006_conversation_history_index"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("mobile_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users", sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("users", sa.Column("deactivation_reason", sa.Text(), nullable=True))
    op.create_index(
        "uq_users_verified_mobile_e164",
        "users",
        ["mobile_number"],
        unique=True,
        postgresql_where=sa.text("mobile_verified_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_users_verified_mobile_e164", table_name="users")
    op.drop_column("users", "deactivation_reason")
    op.drop_column("users", "deactivated_at")
    op.drop_column("users", "mobile_verified_at")
    op.drop_column("users", "email_verified_at")
