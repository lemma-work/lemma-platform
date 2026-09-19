"""Private onboarding, verified platform identities and personal DM destinations."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0038_chat_onboarding"
down_revision = "0037_email_challenges"
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
        # Where this identity talks. Null until a workspace is chosen: being
        # recognised and having somewhere to talk are different things.
        # SET NULL, not CASCADE: removing a company's Slack app should cost its
        # people a destination, not the proof of who they are. PS-SURF-005
        # promises they resume without another email code while their verified
        # identity holds, and a cascade here deletes the row holding it.
        sa.Column(
            "installation_surface_id",
            sa.Uuid(),
            sa.ForeignKey("agent_surfaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "pod_id",
            sa.Uuid(),
            sa.ForeignKey("pods.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # A revoked identity cannot keep a destination. This was two tables
        # keyed on the same binding, which could disagree -- a live route
        # beside a revoked identity was a state every reader had to exclude by
        # hand. Here it is a row the database will not accept.
        sa.CheckConstraint(
            "revoked_at IS NULL OR ("
            "installation_surface_id IS NULL AND pod_id IS NULL)",
            name="ck_surface_identity_route_is_live",
        ),
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
        # `OnboardingStep` says in its own docstring that a typo here "is not an
        # error, it is a state the dispatcher silently has no branch for". This
        # makes it an error. Safe to pin, unlike `agent_surfaces.surface_type`,
        # because these rows are short-lived and internal -- no retired value
        # has to survive in one.
        sa.CheckConstraint(
            "step IN ('handoff', 'awaiting_phone', 'awaiting_email', 'awaiting_code', 'verified', 'awaiting_pod', 'organization_access_required', 'ready', 'cancelled', 'expired')",
            name="ck_pending_onboarding_step",
        ),
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
        sa.Column("offered_pods", postgresql.JSONB(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handed_off_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message_committed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_surface_pending_onboarding_expires_at",
        "surface_pending_onboarding",
        ["expires_at"],
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
    # Both sweeps run every sixty seconds, forever. Without these they are two
    # sequential scans a minute for the life of the deployment.
    op.create_index(
        "ix_surface_onboarding_input_tokens_expires_at",
        "surface_onboarding_input_tokens",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_table("surface_onboarding_input_tokens")
    op.drop_table("surface_pending_onboarding")
    op.drop_table("surface_verified_identities")
