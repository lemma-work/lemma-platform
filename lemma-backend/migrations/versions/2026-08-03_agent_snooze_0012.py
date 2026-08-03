"""Add agent conversation waits: an agent can suspend its own turn.

``ask_user`` and ``request_approval`` already pause a conversation durably — the
run ends, the conversation goes WAITING, and resolving the pending tool call
starts a fresh run that replays the synthesized return from history. What was
missing is a pause that resolves without a person: a timer.

``wait_type`` carries only ``TIME`` today. Waking on a record change was
considered and cut: reacting to a row changing is what a DATASTORE trigger
already does, and a second path to the same event is duplication. The column
stays because a future wake source belongs there rather than in a new table.

This table is what the conversation is waiting on. It is deliberately
shape-identical to ``workflow_run_waits`` (same statuses, same ``external_ref``
discipline, same partial-unique "one ACTIVE per owner" index) because the
reconciliation sweep that self-heals a lost timer is the same sweep, and two
tables that drift apart mean two sweeps that drift apart.

It is NOT a row in ``schedules``. A schedule is a standing rule someone
configured and can pause; this is one suspended execution that resolves exactly
once. Modelling it as a schedule would hand every snooze the consecutive-failure
counter and the circuit breaker that deactivates a schedule after repeated
failures — machinery that is meaningless for a one-shot wait and, in the
breaker's case, actively wrong.

Human pauses stay on ``agent_approval_decisions``. That row records *what the
person said* and who said it; this one records *that execution is suspended*.
Collapsing them before the second wake source exists would be schema written
against a guess.

Revision ID: 0012_agent_snooze
Revises: 0011_connectors_kinds
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0012_agent_snooze"
down_revision = "0011_connectors_kinds"
branch_labels = None
depends_on = None


UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "agent_conversation_waits",
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("conversation_id", UUID, nullable=False),
        sa.Column("agent_run_id", UUID, nullable=False),
        sa.Column("pod_id", UUID, nullable=False),
        # The paused tool call. Resolving the wait synthesizes this call's return,
        # which is what the resumed run replays.
        sa.Column("tool_call_id", sa.String(length=255), nullable=False),
        sa.Column("wait_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        # The scheduler timer that will end this wait.
        sa.Column("external_ref", sa.String(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        # Bumped in its own transaction before each sweep retry, so a wake that
        # rolls back still counts against the cap that eventually abandons it.
        sa.Column(
            "wake_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("spec", JSONB, nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["agent_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pod_id"], ["pods.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_conversation_waits_conversation_id",
        "agent_conversation_waits",
        ["conversation_id"],
    )
    op.create_index(
        "ix_agent_conversation_waits_agent_run_id",
        "agent_conversation_waits",
        ["agent_run_id"],
    )
    op.create_index(
        "ix_agent_conversation_waits_pod_id", "agent_conversation_waits", ["pod_id"]
    )
    op.create_index(
        "ix_agent_conversation_waits_wait_type",
        "agent_conversation_waits",
        ["wait_type"],
    )
    op.create_index(
        "ix_agent_conversation_waits_status", "agent_conversation_waits", ["status"]
    )
    op.create_index(
        "ix_agent_conversation_waits_conversation_status",
        "agent_conversation_waits",
        ["conversation_id", "status"],
    )
    op.create_index(
        "ix_agent_conversation_waits_external_ref",
        "agent_conversation_waits",
        ["external_ref"],
    )
    # The wake path's lookup: resolve a fired timer to exactly one ACTIVE wait.
    op.create_index(
        "ix_agent_conversation_waits_type_ref_status",
        "agent_conversation_waits",
        ["wait_type", "external_ref", "status"],
    )
    # One ACTIVE wait per conversation. A turn that is already snoozed cannot
    # snooze again, and a duplicate wake cannot open a second row.
    op.create_index(
        "uq_agent_conversation_waits_one_active",
        "agent_conversation_waits",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_agent_conversation_waits_one_active",
        table_name="agent_conversation_waits",
    )
    op.drop_index(
        "ix_agent_conversation_waits_type_ref_status",
        table_name="agent_conversation_waits",
    )
    op.drop_index(
        "ix_agent_conversation_waits_external_ref",
        table_name="agent_conversation_waits",
    )
    op.drop_index(
        "ix_agent_conversation_waits_conversation_status",
        table_name="agent_conversation_waits",
    )
    op.drop_index(
        "ix_agent_conversation_waits_status", table_name="agent_conversation_waits"
    )
    op.drop_index(
        "ix_agent_conversation_waits_wait_type", table_name="agent_conversation_waits"
    )
    op.drop_index(
        "ix_agent_conversation_waits_pod_id", table_name="agent_conversation_waits"
    )
    op.drop_index(
        "ix_agent_conversation_waits_agent_run_id",
        table_name="agent_conversation_waits",
    )
    op.drop_index(
        "ix_agent_conversation_waits_conversation_id",
        table_name="agent_conversation_waits",
    )
    op.drop_table("agent_conversation_waits")
