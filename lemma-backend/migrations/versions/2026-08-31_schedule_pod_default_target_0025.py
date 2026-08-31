"""Let a schedule target the pod's default assistant, and carry an instruction.

Two columns, for the two halves of the same gap.

``targets_pod_default`` is the third arm of a schedule's target. The other two
are foreign keys — ``agent_id`` and ``workflow_id`` — and the default assistant
has no ``agents`` row to point at: it is synthesised from a conversation whose
``agent_id`` is null. No sentinel satisfies a real foreign key, so the target is
named by a flag instead. The check constraint keeps the three arms exclusive;
without it "agent_id set *and* targets_pod_default true" is a schedule that
fires twice, discovered months later.

``instruction`` is what the target should *do* when the schedule fires. The
row already had ``filter_instruction``, which decides whether to fire at all;
nothing said what to do afterwards, so a fired agent received the trigger
payload as JSON and had only its own standing instruction to interpret it with.
That is workable for a purpose-built agent and empty for the default assistant,
whose instruction is the empty string. See PS-SCHED-004.
"""

import sqlalchemy as sa
from alembic import op


revision = "0025_schedule_pod_default_target"
down_revision = "0024_drop_surface_schedule_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "schedules",
        sa.Column(
            "targets_pod_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("schedules", sa.Column("instruction", sa.Text(), nullable=True))
    op.create_index(
        "ix_schedules_targets_pod_default",
        "schedules",
        ["targets_pod_default"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_schedules_single_target",
        "schedules",
        "(agent_id IS NOT NULL)::int "
        "+ (workflow_id IS NOT NULL)::int "
        "+ targets_pod_default::int <= 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_schedules_single_target", "schedules", type_="check")
    op.drop_index("ix_schedules_targets_pod_default", table_name="schedules")
    op.drop_column("schedules", "instruction")
    op.drop_column("schedules", "targets_pod_default")
