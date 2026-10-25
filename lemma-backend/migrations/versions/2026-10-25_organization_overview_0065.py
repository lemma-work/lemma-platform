"""What an organization's overview reads: when a member last looked at Home,
and a pod's workflow runs by time.

`organization_members.home_seen_at`: when the member last marked their
organization's Home seen, so it can say what moved since. It lived in the
browser's storage, which a second browser or a cleared profile did not have.
Nullable with no default -- nobody has looked yet -- so adding it rewrites no
row.

`ix_workflow_flow_runs_pod_created`: a pod's runs inside a window, which is how
the overview counts what each workflow ran lately. Without it that read walks
every run a pod ever had through `pod_id` alone, and runs are never pruned.
Built concurrently, so writes to the table do not wait on it.

Revision ID: 0065_organization_overview
Revises: 0064_grants_every_install
"""

import sqlalchemy as sa
from alembic import op

revision = "0065_organization_overview"
down_revision = "0064_grants_every_install"
branch_labels = None
depends_on = None

INDEX = "ix_workflow_flow_runs_pod_created"


def upgrade() -> None:
    op.add_column(
        "organization_members",
        sa.Column("home_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.get_context().autocommit_block():
        op.create_index(
            INDEX,
            "workflow_flow_runs",
            ["pod_id", "created_at"],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            INDEX,
            table_name="workflow_flow_runs",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_column("organization_members", "home_seen_at")
