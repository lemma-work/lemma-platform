"""Channels: one shared place for each chat, with a timeline of who said what.

**`channels`** are the places a pod talks: a member's (DIRECT), a contact's
(CONTACT) or a group's (GROUP). **`channel_sources`** are the outside places
feeding them -- a private chat, a group, an email thread, a web chat -- and are
also the address a surface writes back to (`delivery_ref`) and, for a group,
its settings: who answers for its outsiders, whether they are answered, the
link people join by, and a creation the bot asked a platform for.
**`channel_messages`** is the timeline; a line a person said on a platform
keeps what answers it in place (`reply_ref`). **`channel_participants`** and
**`channel_agents`** are who is in a channel and which agents speak there.

**`agent_conversations.channel_id`** places a conversation in its channel, and
a person has one conversation per agent there.

`agent_surface_conversation_links`, `agent_surface_groups` and
`agent_surface_group_messages` go: sources replace them. Groups and web chat
were unreleased, so nothing is carried over.

Revision ID: 0058_channels
Revises: 0057_contacts_for_everyone
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0058_channels"
down_revision = "0057_contacts_for_everyone"
branch_labels = None
depends_on = None


def _audit() -> list[sa.Column]:
    """``UUIDAuditBase``'s columns, new for each table."""
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def _create_tables() -> None:
    op.create_table(
        "channels",
        *_audit(),
        sa.Column(
            "pod_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column(
            "member_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "handler_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(kind = 'DIRECT') = (member_user_id IS NOT NULL)"
            " AND (kind = 'CONTACT') = (contact_id IS NOT NULL)",
            name="ck_channels_kind_names_its_person",
        ),
    )
    op.create_index(
        "uq_channels_direct_member",
        "channels",
        ["pod_id", "member_user_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'DIRECT'"),
    )
    op.create_index(
        "uq_channels_contact",
        "channels",
        ["pod_id", "contact_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'CONTACT'"),
    )
    op.create_index(
        "ix_channels_pod_activity", "channels", ["pod_id", "last_message_at", "id"]
    )

    op.create_table(
        "channel_participants",
        *_audit(),
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pod_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "num_nonnulls(user_id, contact_id) = 1",
            name="ck_channel_participants_one_person",
        ),
    )
    op.create_index(
        "uq_channel_participants_member",
        "channel_participants",
        ["channel_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "uq_channel_participants_contact",
        "channel_participants",
        ["channel_id", "contact_id"],
        unique=True,
        postgresql_where=sa.text("contact_id IS NOT NULL"),
    )
    op.create_index(
        "ix_channel_participants_member",
        "channel_participants",
        ["user_id", "pod_id"],
    )

    op.create_table(
        "channel_agents",
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "channel_sources",
        *_audit(),
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pod_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "surface_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_surfaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("external_channel_id", sa.String(255), nullable=True),
        sa.Column("external_user_id", sa.String(255), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_ref", JSONB(), nullable=True),
        sa.Column(
            "owner_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "answers_outsiders", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "shared_externally", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("invite_link", sa.String(512), nullable=True),
        sa.Column("request_id", sa.String(255), nullable=True),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_channel_sources_place ON channel_sources "
        "(pod_id, surface_id, platform, coalesce(external_channel_id, ''), "
        "coalesce(external_user_id, '')) WHERE surface_id IS NOT NULL "
        "AND (external_channel_id IS NOT NULL OR external_user_id IS NOT NULL)"
    )
    op.create_index(
        "uq_channel_sources_request",
        "channel_sources",
        ["platform", "request_id"],
        unique=True,
        postgresql_where=sa.text("request_id IS NOT NULL"),
    )
    op.create_index(
        "ix_channel_sources_chat",
        "channel_sources",
        ["platform", "external_channel_id"],
    )
    op.create_index(
        "ix_channel_sources_person",
        "channel_sources",
        ["platform", "external_user_id"],
        postgresql_where=sa.text("kind = 'PRIVATE'"),
    )
    op.create_index(
        "uq_channel_sources_web_place",
        "channel_sources",
        ["pod_id", "platform", "external_channel_id", "external_user_id"],
        unique=True,
        postgresql_where=sa.text("surface_id IS NULL"),
    )
    op.create_index("ix_channel_sources_channel", "channel_sources", ["channel_id"])

    op.create_table(
        "channel_messages",
        *_audit(),
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pod_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            UUID(as_uuid=True),
            sa.ForeignKey("channel_sources.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sender_kind", sa.String(10), nullable=False),
        sa.Column(
            "sender_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "sender_contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "sender_agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "on_behalf_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "on_behalf_contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("sender_name", sa.String(255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("members_only", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("external_message_id", sa.String(255), nullable=True),
        sa.Column("external_thread_id", sa.String(255), nullable=True),
        sa.Column("reply_ref", JSONB(), nullable=True),
        sa.CheckConstraint(
            "num_nonnulls(sender_user_id, sender_contact_id, sender_agent_id) <= 1",
            name="ck_channel_messages_one_sender",
        ),
        sa.CheckConstraint(
            "num_nonnulls(on_behalf_user_id, on_behalf_contact_id) <= 1"
            " AND (sender_kind = 'AGENT'"
            " OR num_nonnulls(on_behalf_user_id, on_behalf_contact_id) = 0)",
            name="ck_channel_messages_only_an_agent_acts_for_someone",
        ),
    )
    op.create_index(
        "ix_channel_messages_timeline",
        "channel_messages",
        ["channel_id", "created_at", "id"],
    )
    op.create_index(
        "uq_channel_messages_external",
        "channel_messages",
        ["source_id", "external_message_id"],
        unique=True,
        postgresql_where=sa.text("external_message_id IS NOT NULL"),
    )
    op.create_index(
        "ix_channel_messages_conversation",
        "channel_messages",
        ["conversation_id", "created_at"],
    )
    op.execute(
        "CREATE INDEX ix_channel_messages_search ON channel_messages "
        "USING gin (to_tsvector('simple', body))"
    )


#: The tables sources replace, as they stood at 0057, for a downgrade.
_OLD_TABLES = """
CREATE TABLE agent_surface_conversation_links (
    surface_id uuid NOT NULL REFERENCES agent_surfaces(id) ON DELETE CASCADE,
    conversation_id uuid NOT NULL
        REFERENCES agent_conversations(id) ON DELETE CASCADE,
    platform character varying(50) NOT NULL,
    external_channel_id character varying(255),
    external_thread_id character varying(255) NOT NULL,
    external_user_id character varying(255),
    routed_agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
    conversation_kind character varying(50) DEFAULT 'DM' NOT NULL,
    route_key character varying(255),
    last_event jsonb NOT NULL,
    last_message_id character varying(255),
    id uuid PRIMARY KEY,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    last_inbound_at timestamp with time zone
);
CREATE INDEX ix_agent_surface_link_conversation
    ON agent_surface_conversation_links (conversation_id);
CREATE UNIQUE INDEX ix_agent_surface_link_external_thread
    ON agent_surface_conversation_links
    (surface_id, platform, external_channel_id, external_thread_id, external_user_id);
CREATE INDEX ix_agent_surface_link_external_user
    ON agent_surface_conversation_links (external_user_id, updated_at);
CREATE INDEX ix_agent_surface_link_surface_member
    ON agent_surface_conversation_links
    (surface_id, external_user_id, last_inbound_at DESC);
CREATE INDEX ix_agent_surface_link_thread_continuity
    ON agent_surface_conversation_links
    (platform, external_thread_id, external_channel_id, external_user_id,
     updated_at DESC);
CREATE TABLE agent_surface_groups (
    id uuid PRIMARY KEY,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    pod_id uuid NOT NULL REFERENCES pods(id) ON DELETE CASCADE,
    surface_id uuid NOT NULL REFERENCES agent_surfaces(id) ON DELETE CASCADE,
    platform character varying(50) NOT NULL,
    external_channel_id character varying(255),
    title character varying(255),
    owner_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    answers_outsiders boolean DEFAULT true NOT NULL,
    request_id character varying(255),
    invite_link text,
    shared_externally boolean DEFAULT false NOT NULL
);
CREATE INDEX ix_agent_surface_group_platform_channel
    ON agent_surface_groups (platform, external_channel_id);
CREATE UNIQUE INDEX ix_agent_surface_group_request
    ON agent_surface_groups (request_id) WHERE request_id IS NOT NULL;
CREATE UNIQUE INDEX ix_agent_surface_group_surface_channel
    ON agent_surface_groups (surface_id, external_channel_id);
CREATE TABLE agent_surface_group_messages (
    id uuid PRIMARY KEY,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    group_id uuid NOT NULL REFERENCES agent_surface_groups(id) ON DELETE CASCADE,
    external_message_id character varying(255),
    author_external_id character varying(255),
    author_name character varying(255),
    from_agent boolean DEFAULT false NOT NULL,
    body text NOT NULL,
    answered_name character varying(255),
    answered_from_public boolean DEFAULT false NOT NULL,
    answered_user_id uuid REFERENCES users(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX ix_agent_surface_group_message_external
    ON agent_surface_group_messages (group_id, external_message_id)
    WHERE external_message_id IS NOT NULL;
CREATE INDEX ix_agent_surface_group_message_recent
    ON agent_surface_group_messages (group_id, created_at DESC);
"""


def upgrade() -> None:
    op.drop_table("agent_surface_group_messages")
    op.drop_table("agent_surface_groups")
    op.drop_table("agent_surface_conversation_links")
    _create_tables()
    op.add_column(
        "agent_conversations",
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Concurrently, outside the transaction: conversations are written on
    # every turn, and a plain build would block those writers.
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_agent_conversations_channel_id",
            "agent_conversations",
            ["channel_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        # A person has one live conversation per agent in a channel.
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
            "uq_agent_conversations_channel_person ON agent_conversations "
            "(channel_id, coalesce(agent_id, pod_id), coalesce(contact_id, user_id)) "
            "WHERE channel_id IS NOT NULL AND parent_id IS NULL AND NOT is_archived"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS uq_agent_conversations_channel_person"
        )
        op.drop_index(
            "ix_agent_conversations_channel_id",
            table_name="agent_conversations",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_column("agent_conversations", "channel_id")
    for table in (
        "channel_messages",
        "channel_sources",
        "channel_agents",
        "channel_participants",
        "channels",
    ):
        op.drop_table(table)
    for statement in _OLD_TABLES.split(";"):
        if statement.strip():
            op.execute(statement)
