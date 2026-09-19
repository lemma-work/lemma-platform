"""Remember a personal workspace independently for every organization.

As a column on the membership it belongs to, rather than a table keyed the same
way. See the note in `upgrade`."""

from alembic import op
import sqlalchemy as sa

revision = "0038_workspace_selections"
down_revision = "0037_email_challenges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A selection is keyed by (user, organization) -- which is a membership row,
    # not a thing of its own. As a table it needed a join back to
    # `organization_members` on every read purely to ask whether the membership
    # still existed; as a column it cannot outlive one.
    op.add_column(
        "organization_members",
        sa.Column(
            "selected_pod_id",
            sa.Uuid(),
            sa.ForeignKey("pods.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("organization_members", "selected_pod_id")
