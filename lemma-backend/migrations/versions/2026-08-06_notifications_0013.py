"""Notifications: an agent can reach a person, and the reply lands somewhere.

Until now an agent could only get something from the person already in front of
it. ``ask_user`` pauses the run and resumes with the answer, but only on the
conversation and surface it is already in — a schedule-born or workflow-born run
has no such person. ``surface.send`` could push a string at a member but needed
an editor permission, persisted nothing, and had no caller.

A notification is deliberately *not* a wait. A wait suspends an execution and
resumes it with an answer; this is fire-and-forget. The sender carries on (and
typically snoozes), and the reply is handled by the *recipient's own* agent, in
their own thread, under their own authority. Nothing moves a value from one
person's run context into another's, so RLS is never asked to do something it
was not asked to authorize.

Two status columns, not one. ``status`` is where the person is; ``delivery_status``
is where the channel is. They are independent — DELIVERED and still OPEN (they
have not answered), UNDELIVERABLE and still RESPONDED (they saw it in the app).
One column smearing both cannot answer "who did we fail to reach?", which is the
only question the delivery axis exists for.

``idempotency_key`` is pod-scoped and closes a real hole: the surface dedup store
claims *inbound* messages only, so without a key here a worker retry double-posts
to a chat platform.

Also adds ``agent_surface_conversation_links.last_inbound_at``. The DM reset rule
keyed off ``updated_at``, which an outbound message also bumps — so a proactive
send would suppress the 24h reset and leak yesterday's context into today. The
backfill is correct by definition: until this revision only inbound events wrote
that row, so ``updated_at`` *was* the last inbound time. It stays nullable
because a row created by an older worker mid-deploy still arrives NULL, and the
reader falls back to ``updated_at`` for exactly that case.

Revision ID: 0013_notifications
Revises: 0012_agent_snooze
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0013_notifications"
down_revision = "0012_agent_snooze"
branch_labels = None
depends_on = None


UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pod_id", UUID, nullable=False),
        sa.Column("recipient_user_id", UUID, nullable=False),
        # Pod-scoped identity, so this matches a workflow FORM node's assignee
        # without a translation step.
        sa.Column("recipient_pod_member_id", UUID, nullable=False),
        # Whose authority the sending run carried. Every delivered message names
        # them: the recipient sees the pod's bot and extends it the trust they
        # extend to Lemma, so an unattributed message is a phishing primitive.
        sa.Column("actor_user_id", UUID, nullable=True),
        sa.Column("actor_agent_id", UUID, nullable=True),
        sa.Column("origin_kind", sa.String(length=30), nullable=False),
        sa.Column("origin_id", UUID, nullable=True),
        sa.Column("origin_conversation_id", UUID, nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        # Addressed to the agent that handles the reply, never rendered to the
        # recipient — it carries the asker's private framing.
        sa.Column("background_instruction", sa.Text(), nullable=True),
        sa.Column(
            "expects_response",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        # For WORKFLOW_FORM: {type, run_id, node_id, schema}. The form executor
        # already resolves the concrete schema onto the wait payload, so the UI
        # and the recipient's agent can both render the real form from here.
        sa.Column("action", JSONB, nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("delivery_status", sa.String(length=30), nullable=False),
        sa.Column("delivery_surface_id", UUID, nullable=True),
        sa.Column("delivery_conversation_id", UUID, nullable=True),
        sa.Column("delivery_platform", sa.String(length=50), nullable=True),
        sa.Column("delivery_error", sa.Text(), nullable=True),
        sa.Column("response_summary", sa.Text(), nullable=True),
        sa.Column("response_data", JSONB, nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["pod_id"], ["pods.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["recipient_pod_member_id"], ["pod_members.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["origin_conversation_id"],
            ["agent_conversations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["delivery_surface_id"], ["agent_surfaces.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["delivery_conversation_id"],
            ["agent_conversations.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        # Pod-scoped rather than global: the key encodes a run/node id, so two
        # pods can never legitimately collide, but a global constraint would make
        # one pod's retry key a landmine for another's.
        sa.UniqueConstraint(
            "pod_id", "idempotency_key", name="uq_notifications_idempotency"
        ),
    )
    op.create_index("ix_notifications_pod_id", "notifications", ["pod_id"])
    op.create_index(
        "ix_notifications_recipient_user_id", "notifications", ["recipient_user_id"]
    )
    op.create_index(
        "ix_notifications_recipient_pod_member_id",
        "notifications",
        ["recipient_pod_member_id"],
    )
    op.create_index("ix_notifications_status", "notifications", ["status"])
    op.create_index(
        "ix_notifications_delivery_status", "notifications", ["delivery_status"]
    )
    # The inbox query.
    op.create_index(
        "ix_notifications_recipient_inbox",
        "notifications",
        ["pod_id", "recipient_user_id", "status", "created_at"],
    )
    # The reply path: does the conversation this inbound landed in have anything
    # open addressed to its owner?
    op.create_index(
        "ix_notifications_delivery_conversation",
        "notifications",
        ["delivery_conversation_id", "status"],
    )
    op.create_index(
        "ix_notifications_origin", "notifications", ["origin_kind", "origin_id"]
    )
    # The expiry sweep only ever scans OPEN rows that carry a deadline.
    op.create_index(
        "ix_notifications_open_expires_at",
        "notifications",
        ["expires_at"],
        postgresql_where=sa.text("status = 'OPEN'"),
    )

    op.add_column(
        "agent_surface_conversation_links",
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_agent_surface_link_last_inbound_at",
        "agent_surface_conversation_links",
        ["last_inbound_at"],
    )
    op.execute(
        "UPDATE agent_surface_conversation_links SET last_inbound_at = updated_at"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_surface_link_last_inbound_at",
        table_name="agent_surface_conversation_links",
    )
    op.drop_column("agent_surface_conversation_links", "last_inbound_at")

    op.drop_index("ix_notifications_open_expires_at", table_name="notifications")
    op.drop_index("ix_notifications_origin", table_name="notifications")
    op.drop_index("ix_notifications_delivery_conversation", table_name="notifications")
    op.drop_index("ix_notifications_recipient_inbox", table_name="notifications")
    op.drop_index("ix_notifications_delivery_status", table_name="notifications")
    op.drop_index("ix_notifications_status", table_name="notifications")
    op.drop_index(
        "ix_notifications_recipient_pod_member_id", table_name="notifications"
    )
    op.drop_index("ix_notifications_recipient_user_id", table_name="notifications")
    op.drop_index("ix_notifications_pod_id", table_name="notifications")
    op.drop_table("notifications")
