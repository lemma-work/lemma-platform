"""Private onboarding, verified platform identities and personal DM destinations."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0033_chat_onboarding"
down_revision = "0032_workspace_selections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "surface_verified_identities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("binding_key", sa.String(64), nullable=False, unique=True),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("external_user_id", sa.String(255), nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("verified_phone", sa.String(32), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_surface_verified_identities_user_id",
        "surface_verified_identities",
        ["user_id"],
    )
    op.create_table(
        "surface_pending_onboarding",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("binding_key", sa.String(64), nullable=False, unique=True),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("step", sa.String(32), nullable=False),
        sa.Column("challenge_id", sa.Uuid(), nullable=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "installation_surface_id",
            sa.Uuid(),
            sa.ForeignKey("agent_surfaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("verified_phone", sa.String(32), nullable=True),
        sa.Column("destination", postgresql.JSONB(), nullable=False),
        sa.Column("original_event", postgresql.JSONB(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handed_off_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message_committed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "surface_personal_dm_routes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("binding_key", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "installation_surface_id",
            sa.Uuid(),
            sa.ForeignKey("agent_surfaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pod_id",
            sa.Uuid(),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assistant_id",
            sa.Uuid(),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )

    op.create_table(
        "surface_onboarding_input_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("challenge_id", sa.Uuid(), nullable=True),
        sa.Column(
            "pending_id",
            sa.Uuid(),
            sa.ForeignKey("surface_pending_onboarding.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("surface_onboarding_input_tokens")
    op.drop_table("surface_personal_dm_routes")
    op.drop_table("surface_pending_onboarding")
    op.drop_table("surface_verified_identities")
