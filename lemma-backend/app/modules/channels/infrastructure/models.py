"""Tables for channels: the place, who is in it, where it is fed from, what was said.

Everything cascades from the pod. A place belongs to an external account -- a
Slack workspace, a Teams tenant, a WhatsApp number, an inbox, a web widget --
so reinstalling a bot keeps its places and their history. Lines are numbered
per channel (``seq``): the order a client reads, and what read state counts.
A question a line asks is its own row, and every send to a place outside Lemma
is a delivery row, unique per line and target.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from app.core.infrastructure.db.base import Base, UUIDAuditBase
from app.modules.channels.infrastructure.answer_models import ChannelLineAnswerModel
from app.modules.channels.infrastructure.case_models import (
    ChannelCaseEventModel,
    ChannelCaseModel,
)


class ExternalAccountModel(UUIDAuditBase):
    """An account a pod's bot speaks through: what a place belongs to."""

    __tablename__ = "external_accounts"
    __table_args__ = (
        # A bot not known yet is one account per workspace, filled in place
        # once it is (``AccountRepository.account_for``).
        UniqueConstraint(
            "pod_id",
            "platform",
            "workspace_key",
            "bot_key",
            name="uq_external_accounts_key",
            postgresql_nulls_not_distinct=True,
        ),
        UniqueConstraint("id", "pod_id", name="uq_external_accounts_id_pod"),
        Index("ix_external_accounts_surface", "surface_id"),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The platform's own name for where the bot is: a Slack team id, a Teams
    #: tenant id, a WhatsApp phone-number id, a Telegram bot id, an inbox
    #: address, a web widget id.
    workspace_key: Mapped[str] = mapped_column(String(320), nullable=False)
    #: Which bot in that workspace, where a workspace holds several (Slack,
    #: Teams); NULL until the bot's id is known, or where there is only one.
    bot_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The installation it is reached through now; another after a reinstall.
    surface_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_surfaces.id", ondelete="SET NULL"), nullable=True
    )


class ChannelModel(UUIDAuditBase):
    __tablename__ = "channels"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('DIRECT', 'CONTACT', 'GROUP')", name="ck_channels_kind"
        ),
        CheckConstraint(
            "visibility IN ('PUBLIC', 'PRIVATE')", name="ck_channels_visibility"
        ),
        CheckConstraint(
            "hand_back_after_hours BETWEEN 1 AND 720",
            name="ck_channels_hand_back_after_hours",
        ),
        CheckConstraint(
            "(kind = 'DIRECT') = (member_user_id IS NOT NULL)"
            " AND (kind = 'CONTACT') = (contact_id IS NOT NULL)",
            name="ck_channels_kind_names_its_person",
        ),
        CheckConstraint(
            "kind = 'GROUP' OR visibility = 'PRIVATE'",
            name="ck_channels_only_a_group_is_public",
        ),
        UniqueConstraint("id", "pod_id", name="uq_channels_id_pod"),
        # For a case's key, which names the channel's kind: only a contact's
        # channel has cases.
        UniqueConstraint("id", "pod_id", "kind", name="uq_channels_id_pod_kind"),
        # The current case is one of the channel's own; a case gone (with its
        # channel) leaves the channel's id alone.
        ForeignKeyConstraint(
            ["current_case_id", "id"],
            ["channel_cases.id", "channel_cases.channel_id"],
            name="fk_channels_current_case",
            ondelete="SET NULL (current_case_id)",
            use_alter=True,
        ),
        # One own channel per member and pod, one channel per contact. Whole,
        # not partial: the kind CHECK makes them the same rule, and a whole
        # index is one a user's or a contact's delete can cascade through.
        Index("uq_channels_direct_member", "member_user_id", "pod_id", unique=True),
        Index("uq_channels_contact", "contact_id", unique=True),
        Index("ix_channels_pod_activity", "pod_id", "last_line_at", "id"),
        # Contacts whose agents are off: waiting on a person (``needs_person``).
        Index(
            "ix_channels_agents_off",
            "pod_id",
            postgresql_where=text("kind = 'CONTACT' AND NOT agents_answer"),
        ),
        Index(
            "ix_channels_created_by",
            "created_by_user_id",
            postgresql_where=text("created_by_user_id IS NOT NULL"),
        ),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    #: ``public``: every member of the pod may read it. ``private``: its
    #: members only. DIRECT and CONTACT are always private, read by their own
    #: rules.
    visibility: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    topic: Mapped[str | None] = mapped_column(String(500), nullable=True)
    member_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: A group's: an agent's answer made with the asking member's own access
    #: is read by everybody in it (posted shared), not by the asker alone
    #: until they share it. Ignored for a DIRECT or CONTACT channel.
    answers_shared: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # -- a contact's channel: who answers them ---------------------------------
    #: Whether its agents answer the contact at all.
    agents_answer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    #: How long an assignee who wrote holds the contact before the agent
    #: answers again.
    hand_back_after_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=12, server_default=text("12")
    )
    #: The case the contact's channel is in now: its open one, or the last
    #: closed (``case_models``). Who answers them, where it stands and whether
    #: it asks for a person are the case's.
    current_case_id: Mapped[UUID | None] = mapped_column(nullable=True)
    current_case: Mapped[ChannelCaseModel | None] = relationship(
        ChannelCaseModel,
        primaryjoin=lambda: (
            ChannelCaseModel.id == foreign(ChannelModel.current_case_id)
        ),
        viewonly=True,
        lazy="selectin",
    )
    # -- the timeline's head ---------------------------------------------------
    #: The newest line's number; the next line takes ``last_seq + 1`` under
    #: this row's lock.
    last_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    #: How many of its lines are replies in a thread: a reader's unread lines
    #: are the timeline's, counted by number without them (``inbox``).
    replies: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    #: The newest line outside a thread, for lists; no FK (it is a pointer
    #: the timeline keeps, not a relation).
    last_line_id: Mapped[UUID | None] = mapped_column(nullable=True)
    last_line_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    #: The channel's change counter, for its live subscribers: every change
    #: they are shown -- a line added, edited, deleted or its question moved,
    #: the channel's own fields, its members -- takes the next number under
    #: this row's lock and is a row of ``channel_changes``, so a subscriber
    #: resumes from the last it saw.
    last_rev: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    #: The change that last moved the channel's own fields or members.
    changed_rev: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )


class ChannelMemberModel(UUIDAuditBase):
    """Who is in a channel: a member, a contact, or an agent."""

    __tablename__ = "channel_members"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(user_id, contact_id, agent_id) = 1",
            name="ck_channel_members_one_principal",
        ),
        CheckConstraint(
            "role IN ('owner', 'member', 'guest')", name="ck_channel_members_role"
        ),
        CheckConstraint(
            "notify IN ('ALL', 'MENTIONS', 'NONE')", name="ck_channel_members_notify"
        ),
        CheckConstraint(
            "(agent_id IS NOT NULL) = (answers IS NOT NULL)"
            " AND (answers IS NULL OR answers IN ('MENTIONED', 'EVERY_LINE'))",
            name="ck_channel_members_agent_answers",
        ),
        CheckConstraint(
            "seated_by IN ('PLATFORM', 'SPOKE', 'ADDED', 'CREATOR', 'JOINED')",
            name="ck_channel_members_seated_by",
        ),
        # The pod's cascade comes through the channel.
        ForeignKeyConstraint(
            ["channel_id", "pod_id"],
            ["channels.id", "channels.pod_id"],
            ondelete="CASCADE",
        ),
        # A channel's seats in the order they were taken: the members page,
        # pushes' recipients, and a channel's delete.
        Index("ix_channel_members_channel", "channel_id", "created_at", "id"),
        Index(
            "uq_channel_members_user",
            "channel_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_channel_members_contact",
            "channel_id",
            "contact_id",
            unique=True,
            postgresql_where=text("contact_id IS NOT NULL"),
        ),
        Index(
            "uq_channel_members_agent",
            "channel_id",
            "agent_id",
            unique=True,
            postgresql_where=text("agent_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_members_user",
            "user_id",
            "pod_id",
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_members_contact",
            "contact_id",
            postgresql_where=text("contact_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_members_agent",
            "agent_id",
            postgresql_where=text("agent_id IS NOT NULL"),
        ),
        # The SET NULL an adder's delete makes.
        Index(
            "ix_channel_members_added_by",
            "added_by_user_id",
            postgresql_where=text("added_by_user_id IS NOT NULL"),
        ),
    )

    channel_id: Mapped[UUID] = mapped_column(nullable=False)
    pod_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True
    )
    role: Mapped[str] = mapped_column(
        String(10), nullable=False, default="member", server_default=text("'member'")
    )
    #: Which lines reach a person outside the app.
    notify: Mapped[str] = mapped_column(
        String(10), nullable=False, default="ALL", server_default=text("'ALL'")
    )
    #: Where they reach them -- platforms they chose -- or ``None`` for where
    #: they last spoke to the pod.
    notify_via: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    muted_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: An agent's: ``MENTIONED`` answers when named, ``EVERY_LINE`` answers
    #: everything said here.
    answers: Mapped[str | None] = mapped_column(String(12), nullable=True)
    added_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Why they are in it (``SeatedBy``): the platform shows them there, they
    #: spoke there, a member added them, they made it, or they joined it.
    #: Only a ``PLATFORM`` seat is the platform's to take back.
    seated_by: Mapped[str] = mapped_column(String(10), nullable=False)
    #: When this person last said something here, and the name they said it
    #: under (as the platform showed it): who has spoken, without the lines.
    last_spoke_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ChannelReadStateModel(Base):
    """How far a member has read a channel: the newest line they have seen.

    Apart from the seat: a member may read a public group they never joined,
    and somebody named in a channel has a place in it to read from."""

    __tablename__ = "channel_read_state"
    __table_args__ = (
        ForeignKeyConstraint(
            ["channel_id", "pod_id"],
            ["channels.id", "channels.pod_id"],
            ondelete="CASCADE",
        ),
        Index("ix_channel_read_state_channel", "channel_id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[UUID] = mapped_column(primary_key=True)
    pod_id: Mapped[UUID] = mapped_column(nullable=False)
    last_read_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    #: How many of the lines up to ``last_read_seq`` are replies in a thread:
    #: what is unread in the timeline is ``last_seq - last_read_seq`` less the
    #: replies since (``ChannelModel.replies - read_replies``).
    read_replies: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ChannelSourceModel(UUIDAuditBase):
    __tablename__ = "channel_sources"
    __table_args__ = (
        CheckConstraint("kind IN ('PERSON', 'CHAT')", name="ck_channel_sources_kind"),
        # Who answers for a group, its invite and the platform's request for
        # it are a chat's; one person's chat has none of them.
        CheckConstraint(
            "kind = 'CHAT' OR (owner_user_id IS NULL AND invite_link IS NULL"
            " AND request_id IS NULL)",
            name="ck_channel_sources_settings_on_a_chat",
        ),
        # For lines' composite foreign keys: a line's place is of its channel.
        UniqueConstraint("id", "channel_id", name="uq_channel_sources_id_channel"),
        # The pod's cascade comes through the channel and the account.
        ForeignKeyConstraint(
            ["channel_id", "pod_id"],
            ["channels.id", "channels.pod_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["account_id", "pod_id"],
            ["external_accounts.id", "external_accounts.pod_id"],
            ondelete="CASCADE",
        ),
        # One row per place an account hears: a chat, or one person in it.
        Index(
            "uq_channel_sources_place",
            "account_id",
            text("coalesce(external_channel_id, '')"),
            text("coalesce(external_user_id, '')"),
            unique=True,
            postgresql_where=text(
                "external_channel_id IS NOT NULL OR external_user_id IS NOT NULL"
            ),
        ),
        # A group the bot asked a platform for has no id yet.
        Index(
            "uq_channel_sources_request",
            "account_id",
            "request_id",
            unique=True,
            postgresql_where=text("request_id IS NOT NULL"),
        ),
        # Just the channel: an inbound's ``last_inbound_at`` stays a HOT update.
        Index("ix_channel_sources_channel", "channel_id"),
        # The account's cascade, and its places.
        Index("ix_channel_sources_account", "account_id"),
        # A pod's groups, most recently changed first (``shared_of_pod``).
        Index(
            "ix_channel_sources_shared",
            "pod_id",
            "updated_at",
            postgresql_where=text("kind = 'CHAT'"),
        ),
        # What every inbound message reads: the chat it was said in, or the
        # person who said it privately.
        Index(
            "ix_channel_sources_chat",
            "external_channel_id",
            "platform",
            postgresql_where=text("external_user_id IS NULL"),
        ),
        Index(
            "ix_channel_sources_person",
            "external_user_id",
            "platform",
            postgresql_where=text("external_user_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_sources_owner",
            "owner_user_id",
            postgresql_where=text("owner_user_id IS NOT NULL"),
        ),
    )

    channel_id: Mapped[UUID] = mapped_column(nullable=False)
    pod_id: Mapped[UUID] = mapped_column(nullable=False)
    account_id: Mapped[UUID] = mapped_column(nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    #: ``PERSON``: one person's chat with the bot. ``CHAT``: several people's.
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    #: The platform keeps it to its members (a private Slack channel, a group
    #: DM, a Teams group chat, a WhatsApp or Telegram group): its channel is
    #: private in Lemma too.
    is_private: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    external_channel_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_inbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: How the account writes into this place when nothing is being answered:
    #: the adapter's own shape.
    delivery_ref: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    # -- a shared place: its bot-side settings ---------------------------------
    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    answers_outsiders: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    shared_externally: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    invite_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


# Kept apart for size, and named here so every reader finds them in one place.
import app.modules.channels.infrastructure.storage  # noqa: E402,F401
from app.modules.channels.infrastructure.change_models import (  # noqa: E402
    ChannelChangeModel,
    ChannelThreadModel,
)
from app.modules.channels.infrastructure.delivery_models import (  # noqa: E402
    ChannelDeliveryAttemptModel,
    ChannelMessageDeliveryModel,
    ChannelPushPendingModel,
)
from app.modules.channels.infrastructure.message_models import (  # noqa: E402
    ChannelMentionModel,
    ChannelMessageModel,
)
from app.modules.channels.infrastructure.question_models import (  # noqa: E402
    ChannelQuestionModel,
)

__all__ = [
    "ChannelCaseEventModel",
    "ChannelCaseModel",
    "ChannelChangeModel",
    "ChannelDeliveryAttemptModel",
    "ChannelLineAnswerModel",
    "ChannelMemberModel",
    "ChannelMentionModel",
    "ChannelMessageDeliveryModel",
    "ChannelMessageModel",
    "ChannelModel",
    "ChannelPushPendingModel",
    "ChannelQuestionModel",
    "ChannelReadStateModel",
    "ChannelSourceModel",
    "ChannelThreadModel",
    "ExternalAccountModel",
]
