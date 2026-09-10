"""Remember a personal workspace independently for every organization."""

from alembic import op
import sqlalchemy as sa

revision = "0032_workspace_selections"
down_revision = "0031_email_challenges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_workspace_selections",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "pod_id",
            sa.Uuid(),
            sa.ForeignKey("pods.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("identity_workspace_selections")
