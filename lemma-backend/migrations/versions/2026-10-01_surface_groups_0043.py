"""Groups a pod's bot is in, who answers for each, and what it heard there.

A bot in a group chat has always answered the pod's members there, each in a
conversation of their own. It has never answered anybody else, and it has never
remembered anything it was not asked: each member's turn saw the group only
through whatever the platform would hand back, which on Telegram is the one
message being replied to and on WhatsApp is nothing.

**`agent_surface_groups`** is one row per group per surface, and its
`owner_user_id` is the member who answers for the group's people from outside
the pod -- who brought the bot in, or who switched outsiders on since. With no
owner, outsiders go unanswered. `SET NULL` on the user: a member leaving takes
that answering-for with them and nothing else.

**`agent_surface_group_messages`** is what the bot heard in the group and what it
said back, read newest-first and always bounded. The unique index on
`(group_id, external_message_id)` is partial, because the bot's own lines carry
no platform message id, and it is what keeps a redelivered webhook from saying
the same thing twice in the log. A line the bot wrote says whom it answered and
whether it answered from what the pod made Public (`answered_name`,
`answered_from_public`), which is how a group's page tells a reader so.

A WhatsApp bot cannot be added to a group; it creates one, and Meta confirms
the creation later, by webhook, naming only the `request_id` it answered. So a
group row can exist before its chat id does: `external_channel_id` is null until
the confirmation lands, `request_id` (unique where set) is how it is found, and
`invite_link` is how people join. `shared_externally` marks a Slack channel
shared with another company, where that company's people are the outsiders.
`(platform, external_channel_id)` is indexed
because that group then belongs to the pod that created it, whichever pods on
the shared number its senders are in.

**`notifications`** gains where a question passed on from outside the pod came
from: `from_outside`, and the title of the group it was asked in and the
asker's name, as they were then.
Such a question's answer goes back to the stranger, so it is recorded only once
the member it was sent to approves the exact words. Every existing row is a
member's or a workflow's, so `from_outside` defaults false and nothing else is
filled in.

Both tables are new and nothing is backfilled: a group becomes known the next
time the bot hears it, which is the only moment the rows are any use.

Revision ID: 0043_surface_groups
Revises: 0042_mcp_access
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0043_surface_groups"
down_revision = "0042_mcp_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_surface_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "pod_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "surface_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_surfaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("external_channel_id", sa.String(255), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "answers_outsiders",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("request_id", sa.String(255), nullable=True),
        sa.Column("invite_link", sa.Text(), nullable=True),
        sa.Column(
            "shared_externally",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_agent_surface_group_surface_channel",
        "agent_surface_groups",
        ["surface_id", "external_channel_id"],
        unique=True,
    )
    op.create_index(
        "ix_agent_surface_group_platform_channel",
        "agent_surface_groups",
        ["platform", "external_channel_id"],
    )
    op.create_index(
        "ix_agent_surface_group_request",
        "agent_surface_groups",
        ["request_id"],
        unique=True,
        postgresql_where=sa.text("request_id IS NOT NULL"),
    )

    op.create_table(
        "agent_surface_group_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_surface_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_message_id", sa.String(255), nullable=True),
        sa.Column("author_external_id", sa.String(255), nullable=True),
        sa.Column("author_name", sa.String(255), nullable=True),
        sa.Column(
            "from_agent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("answered_name", sa.String(255), nullable=True),
        sa.Column(
            "answered_from_public",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "answered_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_agent_surface_group_message_recent",
        "agent_surface_group_messages",
        ["group_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_agent_surface_group_message_external",
        "agent_surface_group_messages",
        ["group_id", "external_message_id"],
        unique=True,
        postgresql_where=sa.text("external_message_id IS NOT NULL"),
    )
    op.add_column(
        "notifications",
        sa.Column(
            "from_outside",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "notifications", sa.Column("origin_group_title", sa.String(255), nullable=True)
    )
    op.add_column(
        "notifications", sa.Column("asked_by_name", sa.String(255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("notifications", "asked_by_name")
    op.drop_column("notifications", "origin_group_title")
    op.drop_column("notifications", "from_outside")
    op.drop_index(
        "ix_agent_surface_group_message_external",
        table_name="agent_surface_group_messages",
    )
    op.drop_index(
        "ix_agent_surface_group_message_recent",
        table_name="agent_surface_group_messages",
    )
    op.drop_table("agent_surface_group_messages")
    op.drop_index("ix_agent_surface_group_request", table_name="agent_surface_groups")
    op.drop_index(
        "ix_agent_surface_group_platform_channel", table_name="agent_surface_groups"
    )
    op.drop_index(
        "ix_agent_surface_group_surface_channel", table_name="agent_surface_groups"
    )
    op.drop_table("agent_surface_groups")
