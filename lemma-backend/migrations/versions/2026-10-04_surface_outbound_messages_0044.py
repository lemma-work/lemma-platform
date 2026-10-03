"""What a surface bot sent, by the id the platform gave it.

A WhatsApp send is accepted synchronously and fails asynchronously: the Graph
API answers with a message id, and minutes later a status webhook may report
that the message never arrived -- most often because the person's 24-hour reply
window had closed, or because the number cannot receive it. That webhook names
the message by id alone. Nothing recorded the ids, so a failure reported that
way could not be traced to anything and was dropped, and the agent went on
believing it had been heard.

**`agent_surface_outbound_messages`** is one row per message the platform
acknowledged, keyed by `(platform, external_message_id)` -- unique, so a status
that arrives twice updates one row. It says which surface sent it, into which
conversation, for which notification if any (`kind` is `REPLY` or
`NOTIFICATION`), to whom, and the text that was sent, which is what a quoted
reply to one of the bot's messages is resolved against. `status` starts `SENT`
and becomes `FAILED` with the platform's `error` when a failure is reported.

The conversation and notification are `SET NULL` rather than `CASCADE`: the row
outliving either is harmless, and a status for it simply finds nothing to act
on. Rows are kept for thirty days (`created_at` is indexed for that sweep);
Meta reports statuses within hours, and a quote of a month-old message is
answered without its text.

The table is new and nothing is backfilled: messages sent before it existed
have no id on record, which is exactly the state every message was in before.

Revision ID: 0044_surface_outbound_messages
Revises: 0043_surface_groups
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044_surface_outbound_messages"
down_revision = "0043_surface_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_surface_outbound_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "surface_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_surfaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "notification_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("notifications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("external_message_id", sa.String(255), nullable=False),
        sa.Column("recipient", sa.String(255), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_agent_surface_outbound_platform_message",
        "agent_surface_outbound_messages",
        ["platform", "external_message_id"],
        unique=True,
    )
    op.create_index(
        "ix_agent_surface_outbound_created",
        "agent_surface_outbound_messages",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_surface_outbound_created",
        table_name="agent_surface_outbound_messages",
    )
    op.drop_index(
        "ix_agent_surface_outbound_platform_message",
        table_name="agent_surface_outbound_messages",
    )
    op.drop_table("agent_surface_outbound_messages")
