"""Durable, bound email verification challenges.

Revision ID: 0031_email_challenges
Revises: 0030_usage_requests
"""

from alembic import op
import sqlalchemy as sa

revision = "0031_email_challenges"
down_revision = "0030_usage_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_email_challenges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.Column("pre_auth_session_id", sa.String(255), nullable=False),
        sa.Column("code_id", sa.String(255), nullable=False),
        sa.Column("device_id", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "attempts >= 0 AND attempts <= 3", name="ck_email_challenge_attempts"
        ),
    )
    op.create_index(
        "ix_identity_email_challenges_email", "identity_email_challenges", ["email"]
    )
    op.create_index(
        "ix_identity_email_challenges_binding_hash",
        "identity_email_challenges",
        ["binding_hash"],
    )


def downgrade() -> None:
    op.drop_table("identity_email_challenges")
