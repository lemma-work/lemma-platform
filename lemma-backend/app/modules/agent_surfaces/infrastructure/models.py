from __future__ import annotations
from app.modules.agent_surfaces.infrastructure.onboarding_models import (  # noqa: F401
    OnboardingInputToken,
    PendingChatOnboarding,
    VerifiedSurfaceIdentity,
)

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import get_secret_cipher
from app.core.log.log import get_logger
from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    AgentSurfaceStatus,
    ExternalSurfaceUserEntity,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.notification import (
    NotificationDeliveryStatus,
    NotificationEntity,
    NotificationOriginKind,
    NotificationStatus,
)

logger = get_logger(__name__)


#: The one value `event_mode` can hold and still name a live way to receive.
#: An allow-list rather than a list of retired values, so a row holding anything
#: unexpected reads as absent instead of as a webhook surface: `COMPOSIO_TRIGGER`
#: went with the polled mailboxes, and no migration deletes those rows on
#: purpose -- they are configuration somebody chose. This was a one-member enum,
#: which is a longer way of writing a constant.
_LIVE_EVENT_MODE = "WEBHOOK"


class AgentSurface(UUIDAuditBase):
    """One agent's connection to one outside platform.

    The owner is the agent, not the pod: `agent_id` is not nullable, and the
    surface answers as that agent and no other. `pod_id` is carried too because
    a surface is reachable only while its pod is, and routing checks that in one
    join rather than per lookup -- so the column is a scope, not the owner.
    """

    __tablename__ = "agent_surfaces"
    __table_args__ = (
        UniqueConstraint("pod_id", "name", name="uq_agent_surface_pod_name"),
        # One agent reaches a platform in exactly one place: one Slack app, one
        # WhatsApp number, one Telegram bot. Declared here as well as in the
        # migration that creates it, so a schema built from metadata carries the
        # same guarantee and autogenerate does not emit a DROP for a constraint
        # it cannot see. The WhatsApp numbers come from a pool and each surface
        # takes one, so without this an agent could quietly hold two of a scarce
        # thing.
        UniqueConstraint(
            "agent_id", "surface_type", name="uq_agent_surface_agent_type"
        ),
        # One agent per pooled WhatsApp number: the arriving number is the
        # routing key once the numbers come from a pool, so two surfaces
        # claiming one is an inbound with no answer to "which agent". Scoped to
        # WhatsApp -- a Slack or Teams bot id may repeat.
        Index(
            "uq_agent_pooled_whatsapp_number",
            "surface_identity_id",
            unique=True,
            postgresql_where=text(
                "surface_type = 'WHATSAPP' AND surface_identity_id IS NOT NULL"
            ),
        ),
        # Address allocation inserts and retries on conflict, which is only safe
        # with this present, and autogenerate would otherwise emit a DROP for an
        # index it cannot see. Functional and partial to match the lookup
        # exactly: inbound routing compares lower(...), and most surfaces are
        # not email and hold NULL here.
        Index(
            "uq_agent_surface_identity_email",
            func.lower(text("surface_identity_email")),
            unique=True,
            postgresql_where=text("surface_identity_email IS NOT NULL"),
        ),
        # Every routing read leads with exactly this pair --
        # `surface_routing_sql.active_surfaces_of_type` -- and `status` had no
        # index at all, so the platform's whole live set was found on
        # `surface_type` alone and then filtered.
        #
        # It stops at the pair on purpose, and that is worth writing down
        # because the obvious next move is wrong. `active_surfaces_of_type` also
        # orders by `(created_at, id)`, the documented tiebreak that picks a
        # surface when a sender resolves to several -- so carrying those two
        # columns looks like it would remove the sort. Measured on 20k rows
        # across five platforms with a seventh inactive, it does not: with no
        # LIMIT the query wants the whole candidate set, and Postgres takes a
        # bitmap scan and sorts regardless. The same index only avoids the sort
        # once a LIMIT applies, which this query does not have. Two extra
        # columns per row for a plan nobody gets.
        #
        # This is also why `surface_type` no longer carries an index of its own:
        # it is this index's leading column, and there is no query that filters
        # the platform without also filtering the status.
        Index("ix_agent_surface_routing", "surface_type", "status"),
    )

    # No index of its own: `uq_agent_surface_pod_name` leads with `pod_id`, and
    # `list_by_pod` is the only reader that filters on it alone.
    pod_id: Mapped[UUID] = mapped_column(ForeignKey("pods.id", ondelete="CASCADE"))
    # Stable, pod-unique identifier addressed by the API (like agent names).
    # Read only as `(pod_id, name)` -- `get_by_pod_and_name` -- which is the
    # unique constraint, so an index on the name alone serves nothing.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Whose surface this is, and the only question this column answers. It used
    # to answer two -- "who answers here by default" inbound and "whose bot is
    # this" outbound -- with null meaning the assistant to one and "nobody's, so
    # anyone may borrow it" to the other. CASCADE rather than SET NULL because
    # a nulled row was indistinguishable from the assistant's own surface, which
    # is how a pod ended up answering from a deleted agent's address.
    # No index of its own: `uq_agent_surface_agent_type` leads with `agent_id`.
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )

    # Leading column of `ix_agent_surface_routing`; see there.
    surface_type: Mapped[str] = mapped_column(String(50))
    # `mode` is gone. It was a two-member enum *derived* from `surface_type`,
    # *validated* against it, then read back to re-derive it -- and nothing
    # outside this module could set it: no API schema had the field, and
    # `SurfaceCreateRequest` forbids extras, so the one place that documented
    # sending `mode=DM` documented a 422.
    #
    # `event_mode` stays as a column and loses its enum: it still filters a row
    # naming a retired way to receive (see `_LIVE_EVENT_MODE`), which is a fact
    # about stored data rather than about the entity.
    event_mode: Mapped[str] = mapped_column(
        String(50), default="WEBHOOK", server_default="WEBHOOK"
    )
    # No index: `credential_mode` appears in two queries, both times as an extra
    # predicate on a read already narrowed by platform and organisation; an
    # index whose most common value matches most of the table is read cost on
    # the write path and nothing on the read path.
    credential_mode: Mapped[str] = mapped_column(
        String(50), default="SYSTEM", server_default="SYSTEM"
    )
    config: Mapped[dict] = mapped_column(JSONB)
    # Indexed: `get_account_conflict_in_org` and the shared-webhook narrowing in
    # `routing_surfaces` both filter it, including as IS NULL.
    account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Indexed: `routing_surfaces` narrows by it on every inbound event that
    # carries a workspace, which is the selective predicate once the platform's
    # live surfaces are the candidate set.
    external_workspace_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    # The four below carry no index, and the reason is the same for all of them:
    # nothing anywhere filters or orders on them. They are read back on a row
    # that was already found. `surface_identity_id` is additionally covered by
    # `uq_agent_pooled_whatsapp_number` for the one lookup that will need it.
    external_tenant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_channel_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    surface_identity_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    surface_identity_username: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(50), default="ACTIVE", server_default="ACTIVE"
    )
    # Its one reader compares `lower(surface_identity_email)`, which a plain
    # btree on the column cannot serve at all. `uq_agent_surface_identity_email`
    # is functional and partial and matches that lookup exactly.
    surface_identity_email: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    # Encrypted at rest via app.core.crypto (compact ``lsenc1:`` envelope). Text
    # (not String(255)) because the envelope is longer than the raw secret.
    webhook_secret: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_entity_or_none(self) -> "AgentSurfaceEntity | None":
        """This row as an entity, or ``None`` when it names something retired.

        ``surface_type`` and ``event_mode`` are plain string columns whose
        enums have both lost members -- ``GMAIL``/``OUTLOOK`` when email became
        Resend and only Resend, ``COMPOSIO_TRIGGER`` when polled triggers went.
        No migration deletes those rows, deliberately: they are configuration
        somebody chose, and removing them belongs to whoever is deploying.

        But `to_entity` raising a bare ``ValueError`` made that choice
        everyone's problem. `list_by_pod` maps a whole page, so one retired row
        took the pod's entire surface list with it -- as a 500, since a
        ``ValueError`` is not a ``DomainError`` and reaches the catch-all
        handler. `get_account_conflict_in_org` is org-wide and platform-blind,
        so it did the same to the *creation* of an unrelated surface, and
        `list_by_pod` again to every agent-to-human notification for the pod.

        So: unreadable rows drop out of lists and read as absent, which is what
        every caller already handles. `to_entity` still raises, because code
        holding a validated entity is entitled to assume the mapping worked.
        """
        raw_type = (self.surface_type or "SLACK").rsplit(".", 1)[-1].upper()
        if SurfacePlatform.from_source(raw_type) is None:
            self._log_retired_value("surface_type", raw_type)
            return None
        raw_event_mode = str(self.event_mode or _LIVE_EVENT_MODE).upper()
        if raw_event_mode != _LIVE_EVENT_MODE:
            self._log_retired_value("event_mode", raw_event_mode)
            return None
        return self.to_entity()

    def _log_retired_value(self, column: str, value: str) -> None:
        logger.warning(
            "agent_surfaces.surface_row.retired_value_skipped.degraded",
            surface_id=str(self.id),
            column=column,
            value=value,
        )

    def to_entity(self) -> AgentSurfaceEntity:
        surface_type_raw = self.surface_type or "SLACK"
        if "." in surface_type_raw:
            surface_type_raw = surface_type_raw.rsplit(".", 1)[-1]

        return AgentSurfaceEntity(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            pod_id=self.pod_id,
            name=self.name or surface_type_raw.lower(),
            agent_id=self.agent_id,
            surface_type=SurfacePlatform(surface_type_raw.upper()),
            credential_mode=SurfaceCredentialMode(
                self.credential_mode or SurfaceCredentialMode.SYSTEM.value
            ),
            config=self.config,
            account_id=self.account_id,
            external_workspace_id=self.external_workspace_id,
            external_tenant_id=self.external_tenant_id,
            external_channel_id=self.external_channel_id,
            surface_identity_id=self.surface_identity_id,
            surface_identity_username=self.surface_identity_username,
            status=self.status or AgentSurfaceStatus.ACTIVE.value,
            surface_identity_email=self.surface_identity_email,
            # Decrypt at rest; legacy plaintext rows pass through unchanged.
            webhook_secret=get_secret_cipher().decrypt_str(self.webhook_secret),
        )


class AgentSurfaceExternalUser(UUIDAuditBase):
    __tablename__ = "agent_surface_external_users"
    __table_args__ = (
        # NULLS NOT DISTINCT: Telegram writes no tenant, and by default
        # Postgres would treat every one of those NULLs as a different value --
        # so the uniqueness this index exists for never applied to it. Declared
        # here too so a schema built from metadata carries the same guarantee.
        # Why the repository cannot work without it is recorded on
        # `ExternalSurfaceUserRepository`, which is what depends on it.
        Index(
            "ix_agent_surface_external_user_platform_tenant_external",
            "platform",
            "tenant_id",
            "external_user_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        # `get_by_email` compares `lower(email)`, so the plain btree this
        # replaces could never be used for it -- the one query the column had.
        # Paired with the platform because that is how the query asks.
        Index(
            "ix_agent_surface_external_user_platform_email",
            "platform",
            func.lower(text("email")),
            postgresql_where=text("email IS NOT NULL"),
        ),
        # `list_by_resolved_users` reads `resolved_user_id IN (...) AND platform
        # = ...`; `clear_resolved_users` updates on `resolved_user_id` alone,
        # which this index's leading column serves.
        Index(
            "ix_agent_surface_external_user_resolved_platform",
            "resolved_user_id",
            "platform",
        ),
    )

    # No index of its own: leading column of the unique triple above.
    platform: Mapped[str] = mapped_column(String(50))
    tenant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Never filtered or ordered on anywhere. It is written by profile enrichment
    # and read back off a row found by platform identity.
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_profile: Mapped[dict] = mapped_column(JSONB, default=dict)
    resolved_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def to_entity(self) -> ExternalSurfaceUserEntity:
        return ExternalSurfaceUserEntity.model_validate(self)


class AgentSurfaceConversationLinkModel(UUIDAuditBase):
    __tablename__ = "agent_surface_conversation_links"
    __table_args__ = (
        Index(
            "ix_agent_surface_link_external_thread",
            "surface_id",
            "platform",
            "external_channel_id",
            "external_thread_id",
            "external_user_id",
            unique=True,
        ),
        Index("ix_agent_surface_link_conversation", "conversation_id"),
        # `find_surface_id_for_external_thread`: the continuity lookup that runs
        # on *every* inbound message, and the one read here that is not scoped
        # to a surface -- it exists to find which surface a returning chat
        # already lives on. Without this it matched the standalone `platform`
        # index and then filtered and sorted a table that grows per thread.
        # Column order is the query's; `updated_at DESC` is its `ORDER BY ...
        # LIMIT 1`.
        Index(
            "ix_agent_surface_link_thread_continuity",
            "platform",
            "external_thread_id",
            "external_channel_id",
            "external_user_id",
            text("updated_at DESC"),
        ),
        # `latest_links_by_surface_and_external_users`: the fan-in read, one row
        # per person via DISTINCT ON. The pair is the lookup; `last_inbound_at`
        # is carried because the recency it sorts by is
        # `coalesce(last_inbound_at, updated_at)`, so this covers the common
        # case without claiming to serve the coalesce itself.
        Index(
            "ix_agent_surface_link_surface_member",
            "surface_id",
            "external_user_id",
            text("last_inbound_at DESC"),
        ),
    )

    # No index of its own: leading column of `ix_agent_surface_link_external_thread`,
    # and paired with the member in `ix_agent_surface_link_surface_member`.
    surface_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_surfaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    # `index=True` removed, not the index: it produced a second, identical index
    # beside `ix_agent_surface_link_conversation` above. Both existed, both were
    # maintained on every write, and one of them could ever be chosen.
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # No index of its own: leading column of the continuity index above, which
    # is the only read that starts from the platform.
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    external_channel_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Never filtered on: it records which agent answered this thread and is read
    # off a row already found. Same for `route_key` below.
    routed_agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    conversation_kind: Mapped[str] = mapped_column(
        String(50), default="DM", server_default="DM", nullable=False
    )
    route_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_event: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Nullable, and it stays nullable: the backfill sets it from ``updated_at``
    # for existing rows, but a row created by an older worker mid-deploy would
    # still arrive NULL. ``inbound_activity_at`` on the entity is the reader.
    last_inbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def to_entity(self) -> AgentSurfaceConversationLink:
        return AgentSurfaceConversationLink(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            surface_id=self.surface_id,
            conversation_id=self.conversation_id,
            platform=self.platform,
            external_channel_id=self.external_channel_id,
            external_thread_id=self.external_thread_id,
            external_user_id=self.external_user_id,
            routed_agent_id=self.routed_agent_id,
            conversation_kind=self.conversation_kind or "DM",
            route_key=self.route_key,
            last_event=self.last_event or {},
            last_message_id=self.last_message_id,
            last_inbound_at=self.last_inbound_at,
        )


class NotificationModel(UUIDAuditBase):
    """Something the pod needs a person to see — see ``domain/notification.py``.

    Lives in ``agent_surfaces`` because delivery is almost entirely surface work
    (identity resolution, conversation links, platform adapters all live here).
    The agent and workflow modules reach it through ports in ``app/composition``,
    the same way they reach the scheduler.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        # The inbox query: this person's notifications in this pod, newest first.
        Index(
            "ix_notifications_recipient_inbox",
            "pod_id",
            "recipient_user_id",
            "status",
            "created_at",
        ),
        # The reply path: does the conversation this inbound landed in have
        # anything open addressed to its owner?
        Index(
            "ix_notifications_delivery_conversation",
            "delivery_conversation_id",
            "status",
        ),
        Index("ix_notifications_origin", "origin_kind", "origin_id"),
        # The expiry sweep only ever scans OPEN rows with a deadline.
        Index(
            "ix_notifications_open_expires_at",
            "expires_at",
            postgresql_where=text("status = 'OPEN'"),
        ),
        # Pod-scoped rather than global: the key encodes a run/node id, and two
        # pods can legitimately never collide, but a global unique index would
        # make one pod's retry key a landmine for another's.
        UniqueConstraint(
            "pod_id", "idempotency_key", name="uq_notifications_idempotency"
        ),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), index=True, nullable=False
    )
    recipient_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    recipient_pod_member_id: Mapped[UUID] = mapped_column(
        ForeignKey("pod_members.id", ondelete="CASCADE"), index=True, nullable=False
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    origin_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    origin_id: Mapped[UUID | None] = mapped_column(nullable=True)
    origin_conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    background_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    expects_response: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    action: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    delivery_status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    delivery_surface_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_surfaces.id", ondelete="SET NULL"), nullable=True
    )
    delivery_conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"), nullable=True
    )
    delivery_platform: Mapped[str | None] = mapped_column(String(50), nullable=True)
    delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    response_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def to_entity(self) -> NotificationEntity:
        return NotificationEntity(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            pod_id=self.pod_id,
            recipient_user_id=self.recipient_user_id,
            recipient_pod_member_id=self.recipient_pod_member_id,
            actor_user_id=self.actor_user_id,
            actor_agent_id=self.actor_agent_id,
            origin_kind=NotificationOriginKind(self.origin_kind),
            origin_id=self.origin_id,
            origin_conversation_id=self.origin_conversation_id,
            title=self.title,
            body=self.body,
            background_instruction=self.background_instruction,
            expects_response=self.expects_response,
            action=self.action,
            status=NotificationStatus(self.status),
            delivery_status=NotificationDeliveryStatus(self.delivery_status),
            delivery_surface_id=self.delivery_surface_id,
            delivery_conversation_id=self.delivery_conversation_id,
            delivery_platform=self.delivery_platform,
            delivery_error=self.delivery_error,
            response_summary=self.response_summary,
            response_data=self.response_data,
            idempotency_key=self.idempotency_key,
            expires_at=self.expires_at,
            delivered_at=self.delivered_at,
            read_at=self.read_at,
            responded_at=self.responded_at,
        )
