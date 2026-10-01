"""The group tables: which groups a bot is in, and what it heard there.

Mirrors ``migrations/versions/2026-09-30_surface_groups_0042.py`` column for
column and index name for index name, for the reason ``whatsapp_pool_models``
gives: a schema built from metadata must carry the migration's guarantees, and
anything declared in one place only is something autogenerate would offer to
drop. Its own module because ``models.py`` is at the size ceiling.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.agent_surfaces.domain.groups import SurfaceGroup


class AgentSurfaceGroupModel(UUIDAuditBase):
    __tablename__ = "agent_surface_groups"
    __table_args__ = (
        # One row per group per surface: the key every read starts from. The
        # same chat can hold two pods' bots, and each answers for its own.
        Index(
            "ix_agent_surface_group_surface_channel",
            "surface_id",
            "external_channel_id",
            unique=True,
        ),
        # Which surface a group belongs to, asked by the group alone: a
        # WhatsApp group is the pod's that created it, whichever of the pods
        # on the shared number its sender is in.
        Index(
            "ix_agent_surface_group_platform_channel",
            "platform",
            "external_channel_id",
        ),
        # A creation's confirmation names only the request it answers.
        Index(
            "ix_agent_surface_group_request",
            "request_id",
            unique=True,
            postgresql_where=text("request_id IS NOT NULL"),
        ),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    surface_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_surfaces.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    # Null only while a creation the bot asked for is unconfirmed.
    external_channel_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # SET NULL, not CASCADE: a member leaving takes their answering-for with
    # them, and nothing else -- the group and its log stay, and outsiders simply
    # go unanswered until somebody takes it on.
    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    answers_outsiders: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invite_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    shared_externally: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    def to_entity(self) -> SurfaceGroup:
        return SurfaceGroup(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            pod_id=self.pod_id,
            surface_id=self.surface_id,
            platform=self.platform,
            external_channel_id=self.external_channel_id,
            title=self.title,
            owner_user_id=self.owner_user_id,
            answers_outsiders=self.answers_outsiders,
            request_id=self.request_id,
            invite_link=self.invite_link,
            shared_externally=self.shared_externally,
        )


class AgentSurfaceGroupMessageModel(UUIDAuditBase):
    __tablename__ = "agent_surface_group_messages"
    __table_args__ = (
        # The only read: a group's most recent lines, newest first, bounded.
        Index(
            "ix_agent_surface_group_message_recent",
            "group_id",
            text("created_at DESC"),
        ),
        # A redelivered webhook must not say the same thing twice in the log.
        # Partial, because the bot's own lines have no platform message id.
        Index(
            "ix_agent_surface_group_message_external",
            "group_id",
            "external_message_id",
            unique=True,
            postgresql_where=text("external_message_id IS NOT NULL"),
        ),
    )

    group_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_surface_groups.id", ondelete="CASCADE"), nullable=False
    )
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_agent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    answered_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    answered_from_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: The member whose access an answer was made with. The line outlives the
    #: account, and reads as withheld from everyone once it is gone.
    answered_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
