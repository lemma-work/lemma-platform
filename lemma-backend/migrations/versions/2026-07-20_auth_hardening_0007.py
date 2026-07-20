"""Add identity verification, deactivation, and email suppression state.

Revision ID: 0007_auth_hardening
Revises: 0006_conversation_history_index
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision = "0007_auth_hardening"
down_revision = "0006_conversation_history_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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

    op.create_table(
        "identity_email_suppressions",
        sa.Column("normalized_email", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("evidence_source", sa.String(length=64), nullable=False),
        sa.Column("is_permanent", sa.Boolean(), nullable=False),
        sa.Column("provider_event_id", sa.String(length=255), nullable=True),
        sa.Column("diagnostic", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_identity_email_suppressions_id",
        "identity_email_suppressions",
        ["id"],
        unique=False,
    )
    op.create_index(
        "uq_identity_email_suppressions_email_lower",
        "identity_email_suppressions",
        [sa.text("lower(normalized_email)")],
        unique=True,
    )
    op.create_index(
        "uq_identity_email_suppressions_provider_event",
        "identity_email_suppressions",
        ["provider_event_id"],
        unique=True,
        postgresql_where=sa.text("provider_event_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("identity_email_suppressions")
    op.drop_index("uq_users_verified_mobile_e164", table_name="users")
    op.drop_column("users", "deactivation_reason")
    op.drop_column("users", "deactivated_at")
    op.drop_column("users", "mobile_verified_at")
    op.drop_column("users", "email_verified_at")
