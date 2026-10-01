"""Threads on a platform, and the conversations they map to.

One table, `agent_surface_conversation_links`, and the three questions asked of
it: which conversation is this exact chat, which surface does a returning chat
already live on, and which thread is this person's on this surface.

Its own file because the directory's unit is one repository per table --
`external_user_repository`, `notification_repository` -- and this had been
sharing `surface_repository.py` with the installations repository, which is a
different table answering different questions. The two share nothing but
imports.

Every index these reads need is named by the query in
`infrastructure/models.py`, beside its declaration.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Sequence
from typing import Any
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.domain.uow import IUnitOfWork
from app.core.infrastructure.db.transaction_locks import mark_transaction_scoped_lock
from app.modules.agent_surfaces.domain.entities import AgentSurfaceConversationLink
from app.modules.agent_surfaces.domain.groups import OUTSIDERS_LINK_USER
from app.modules.agent_surfaces.domain.notification import (
    NotificationDeliveryStatus,
    NotificationStatus,
)
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurfaceConversationLinkModel,
    NotificationModel,
)


def _thread_lock_key(
    surface_id: UUID,
    platform: str,
    external_channel_id: str | None,
    external_thread_id: str,
    external_user_id: str | None,
) -> int:
    """A stable signed 64-bit lock key for one exact chat on one surface.

    A missing channel or user is not the same as an empty one, so each part is
    tagged rather than joined bare. Hashed for the same reason
    `surface_repository._identity_claim_lock_key` is: the key says nothing about
    the chat it locks, and a long id cannot overflow the space.
    """
    parts = [
        str(surface_id),
        platform,
        "-" if external_channel_id is None else f"+{external_channel_id}",
        external_thread_id,
        "-" if external_user_id is None else f"+{external_user_id}",
    ]
    digest = hashlib.blake2b(
        "\x00".join(parts).encode(), digest_size=8, person=b"lemma-thread"
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


class SurfaceConversationLinkRepository:
    """Repository for external platform threads mapped to agent conversations."""

    def __init__(self, uow: IUnitOfWork):
        self.uow = uow
        self.session: Session = uow.session

    async def get_by_external_thread(
        self,
        *,
        surface_id: UUID,
        platform: str,
        external_channel_id: str | None,
        external_thread_id: str,
        external_user_id: str | None,
    ) -> AgentSurfaceConversationLink | None:
        stmt = select(AgentSurfaceConversationLinkModel).where(
            AgentSurfaceConversationLinkModel.surface_id == surface_id,
            AgentSurfaceConversationLinkModel.platform == platform,
            AgentSurfaceConversationLinkModel.external_thread_id == external_thread_id,
        )
        if external_channel_id is None:
            stmt = stmt.where(
                AgentSurfaceConversationLinkModel.external_channel_id.is_(None)
            )
        else:
            stmt = stmt.where(
                AgentSurfaceConversationLinkModel.external_channel_id
                == external_channel_id
            )
        if external_user_id is None:
            stmt = stmt.where(
                AgentSurfaceConversationLinkModel.external_user_id.is_(None)
            )
        else:
            stmt = stmt.where(
                AgentSurfaceConversationLinkModel.external_user_id == external_user_id
            )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def find_surface_id_for_external_thread(
        self,
        *,
        platform: str,
        external_channel_id: str | None,
        external_thread_id: str,
        external_user_id: str | None,
        surface_ids: Collection[UUID] | None = None,
    ) -> UUID | None:
        """The surface an existing conversation for this exact chat lives on.

        Same match shape as ``get_by_external_thread`` but not scoped to one
        surface -- it is what keeps a returning chat on the surface it first
        landed on, so a sender reachable via a shared bot across several pods
        does not bounce between them. Returns the most-recently-updated link's
        surface id, or None when the chat is new.

        ``surface_ids`` narrows it to a candidate set, and routing passes one.
        It changes an answer rather than a cost, which is why it is here rather
        than left to the caller's filter: unnarrowed this returns the freshest
        link *anywhere on the platform*, and the caller then keeps it only if it
        is a candidate. So a fresher link on a surface that is no longer a
        candidate -- switched off, or not served by the bot that delivered this
        event -- returns an id the caller discards, and the person's real
        ongoing conversation, on a candidate surface, is never found. The chat
        then falls to the deterministic tiebreak and answers from a different
        pod. Narrowed, continuity is the freshest thread *among the candidates*,
        which is what every caller wanted.

        The interaction path passes nothing, and should: a tapped button asks
        which surface owns this chat, with no candidate set to be inside.
        """
        stmt = select(AgentSurfaceConversationLinkModel.surface_id).where(
            AgentSurfaceConversationLinkModel.platform == platform,
            AgentSurfaceConversationLinkModel.external_thread_id == external_thread_id,
        )
        if surface_ids is not None:
            stmt = stmt.where(
                AgentSurfaceConversationLinkModel.surface_id.in_(list(surface_ids))
            )
        if external_channel_id is None:
            stmt = stmt.where(
                AgentSurfaceConversationLinkModel.external_channel_id.is_(None)
            )
        else:
            stmt = stmt.where(
                AgentSurfaceConversationLinkModel.external_channel_id
                == external_channel_id
            )
        if external_user_id is None:
            stmt = stmt.where(
                AgentSurfaceConversationLinkModel.external_user_id.is_(None)
            )
        else:
            stmt = stmt.where(
                AgentSurfaceConversationLinkModel.external_user_id == external_user_id
            )
        stmt = stmt.order_by(AgentSurfaceConversationLinkModel.updated_at.desc()).limit(
            1
        )
        return await self.session.scalar(stmt)

    async def find_latest_dm_link_for_person(
        self,
        *,
        platform: str,
        external_user_id: str,
        surface_ids: Collection[UUID],
    ) -> AgentSurfaceConversationLink | None:
        """This person's most recent private-chat link on any of these surfaces.

        The exact-thread reads above key on a delivery address -- the surface, the
        channel and the thread id -- and for a private chat the address is not what
        makes it the same conversation. On WhatsApp it embeds the number the
        message arrived on, so a reassigned number or a different serving surface
        reads as a chat nobody has spoken in. This is the person-level answer to
        "is there already a conversation here", and it is asked only after the
        exact one has missed.

        ``surface_ids`` is required, and is what keeps this read on
        ``ix_agent_surface_link_surface_member`` (surface, person, recency):
        without a surface list the same question would scan every link of the
        platform. Ordered by inbound recency for the reason
        ``list_latest_by_surface_and_external_users`` gives.
        """
        if not surface_ids:
            return None
        recency = func.coalesce(
            AgentSurfaceConversationLinkModel.last_inbound_at,
            AgentSurfaceConversationLinkModel.updated_at,
        )
        stmt = (
            select(AgentSurfaceConversationLinkModel)
            .where(
                AgentSurfaceConversationLinkModel.platform == platform,
                AgentSurfaceConversationLinkModel.external_user_id == external_user_id,
                AgentSurfaceConversationLinkModel.conversation_kind == "DM",
                AgentSurfaceConversationLinkModel.surface_id.in_(list(surface_ids)),
            )
            .order_by(recency.desc())
            .limit(1)
        )
        model = (await self.session.execute(stmt)).scalar_one_or_none()
        return model.to_entity() if model else None

    async def rebind_thread_address(
        self,
        *,
        link_id: UUID,
        surface_id: UUID,
        external_channel_id: str | None,
        external_thread_id: str,
    ) -> AgentSurfaceConversationLink | None:
        """Point an existing link at the address its chat is now delivered on.

        The conversation, the person and the agent are untouched: only where the
        chat is reached changes. The caller holds the thread lock for the new
        address and has read that nothing lives there, so the unique index cannot
        be met.
        """
        model = await self.session.get(AgentSurfaceConversationLinkModel, link_id)
        if model is None:
            return None
        model.surface_id = surface_id
        model.external_channel_id = external_channel_id
        model.external_thread_id = external_thread_id
        await self.session.flush()
        return model.to_entity()

    async def get_latest_by_surface_and_external_user(
        self,
        *,
        surface_id: UUID,
        external_user_id: str,
    ) -> AgentSurfaceConversationLink | None:
        """The member's most recent thread on a surface.

        ``surface.send`` and notification delivery reuse this existing thread
        (and its valid reply target) to reach a member proactively — bots can't
        cold-DM, so a prior interaction is required.

        One member's slice of ``list_latest_by_surface_and_external_users``,
        which owns the ordering — see there for why it is inbound recency.
        """
        links = await self.list_latest_by_surface_and_external_users(
            surface_id=surface_id, external_user_ids=[external_user_id]
        )
        return links.get(external_user_id)

    async def list_latest_by_surface_and_external_users(
        self,
        *,
        surface_id: UUID,
        external_user_ids: Sequence[str],
    ) -> dict[str, AgentSurfaceConversationLink]:
        """``{external_user_id: their most recent thread}`` on one surface.

        Ordered by inbound recency, not ``updated_at``: an outbound message also
        bumps ``updated_at``, so ranking by it would mean "the thread we last
        talked *at* them on" rather than "the thread they last talked to us on".
        Only the second is evidence of where they are actually looking. COALESCE
        keeps pre-migration rows, where the two were the same thing, in the sort.

        DISTINCT ON picks per person in the database rather than dragging a busy
        surface's whole history back to reduce it here. The single-member form
        delegates to this one so a reachability check and the send that follows
        it can never disagree about which thread is theirs.

        Private threads only. Every caller is reaching one person -- a
        notification, a message the pod sends them -- and a group thread is
        where they last *spoke*, not somewhere private to reach them: the reply
        goes to the group, in front of everyone in it. A member whose only
        thread is a group is therefore unreachable here, and delivery falls
        back to email or their Lemma inbox, which is the right answer.
        """
        if not external_user_ids:
            return {}
        recency = func.coalesce(
            AgentSurfaceConversationLinkModel.last_inbound_at,
            AgentSurfaceConversationLinkModel.updated_at,
        )
        stmt = (
            select(AgentSurfaceConversationLinkModel)
            .where(
                AgentSurfaceConversationLinkModel.surface_id == surface_id,
                AgentSurfaceConversationLinkModel.external_user_id.in_(
                    external_user_ids
                ),
                AgentSurfaceConversationLinkModel.conversation_kind != "CHANNEL",
            )
            .distinct(AgentSurfaceConversationLinkModel.external_user_id)
            .order_by(
                AgentSurfaceConversationLinkModel.external_user_id,
                recency.desc(),
            )
        )
        result = await self.session.execute(stmt)
        return {
            link.external_user_id: link
            for link in (model.to_entity() for model in result.scalars().all())
            if link.external_user_id
        }

    async def conversation_ids_in_channel(
        self,
        *,
        surface_id: UUID,
        external_channel_id: str,
        external_user_id: str,
        limit: int = 20,
    ) -> list[UUID]:
        """The conversations one sender key holds in one channel, newest first.

        For a group's people outside the pod the key is shared, and a platform
        with threads (Slack) gives them one conversation per thread.
        """
        stmt = (
            select(AgentSurfaceConversationLinkModel.conversation_id)
            .where(
                AgentSurfaceConversationLinkModel.surface_id == surface_id,
                AgentSurfaceConversationLinkModel.external_channel_id
                == external_channel_id,
                AgentSurfaceConversationLinkModel.external_user_id == external_user_id,
            )
            .order_by(AgentSurfaceConversationLinkModel.updated_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_by_conversation_id(
        self,
        conversation_id: UUID,
    ) -> AgentSurfaceConversationLink | None:
        stmt = (
            select(AgentSurfaceConversationLinkModel)
            .where(AgentSurfaceConversationLinkModel.conversation_id == conversation_id)
            .order_by(AgentSurfaceConversationLinkModel.updated_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def is_outsiders_thread(self, conversation_id: UUID) -> bool:
        """Whether routing linked this conversation as the strangers' thread."""
        link = AgentSurfaceConversationLinkModel
        stmt = select(
            select(link.id)
            .where(link.conversation_id == conversation_id)
            .where(link.external_user_id == OUTSIDERS_LINK_USER)
            .exists()
        )
        return bool((await self.session.execute(stmt)).scalar())

    async def lock_thread(
        self,
        *,
        surface_id: UUID,
        platform: str,
        external_channel_id: str | None,
        external_thread_id: str,
        external_user_id: str | None,
    ) -> None:
        """Serialise first messages on one chat until this transaction ends.

        Two rapid first messages both read "no link yet" and both went on to open
        a conversation, so the second insert either hit the unique index or --
        with a missing channel or user, which a plain unique index treats as
        always distinct -- quietly made a second link that every later read of
        the chat then failed on. Taking this before the *second* read makes the
        loser wait for the winner's commit and then find its link, without
        having opened a conversation it would have to throw away.
        """
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {
                "lock_key": _thread_lock_key(
                    surface_id,
                    platform,
                    external_channel_id,
                    external_thread_id,
                    external_user_id,
                )
            },
        )
        # Released at commit, so a connection-scope release in between would drop
        # it mid-bind; the mark is what stops that.
        mark_transaction_scoped_lock(self.session)

    async def create(
        self,
        link: AgentSurfaceConversationLink,
        *,
        locked: bool = False,
    ) -> AgentSurfaceConversationLink:
        """Insert the link, or return the one that beat this to it.

        Takes the thread lock itself, because this is also reached from
        notification delivery, which does not go through the binder. The lock is
        what makes "at most one link per chat" true: the unique index treats a
        missing channel or user as always distinct, so for a direct chat it
        cannot say so, and the lock is held to commit so a loser sees the
        winner's row when it re-reads. ``locked`` says the caller has already
        taken it and read, so neither is repeated. ``ON CONFLICT DO NOTHING`` still covers
        the chats that do have every part of the key.
        """
        if not locked:
            await self.lock_thread(
                surface_id=link.surface_id,
                platform=link.platform,
                external_channel_id=link.external_channel_id,
                external_thread_id=link.external_thread_id,
                external_user_id=link.external_user_id,
            )
            existing = await self.get_by_external_thread(
                surface_id=link.surface_id,
                platform=link.platform,
                external_channel_id=link.external_channel_id,
                external_thread_id=link.external_thread_id,
                external_user_id=link.external_user_id,
            )
            if existing is not None:
                return existing
        statement = (
            pg_insert(AgentSurfaceConversationLinkModel)
            .values(
                id=link.id,
                created_at=link.created_at,
                updated_at=link.updated_at,
                surface_id=link.surface_id,
                conversation_id=link.conversation_id,
                platform=link.platform,
                external_channel_id=link.external_channel_id,
                external_thread_id=link.external_thread_id,
                external_user_id=link.external_user_id,
                routed_agent_id=link.routed_agent_id,
                conversation_kind=link.conversation_kind,
                route_key=link.route_key,
                last_event=link.last_event,
                last_message_id=link.last_message_id,
                last_inbound_at=link.last_inbound_at,
            )
            .on_conflict_do_nothing()
            .returning(AgentSurfaceConversationLinkModel)
        )
        inserted = (await self.session.execute(statement)).scalar_one_or_none()
        if inserted is not None:
            return inserted.to_entity()
        existing = await self.get_by_external_thread(
            surface_id=link.surface_id,
            platform=link.platform,
            external_channel_id=link.external_channel_id,
            external_thread_id=link.external_thread_id,
            external_user_id=link.external_user_id,
        )
        if existing is None:
            raise RuntimeError(
                "conversation link insert conflicted but no link exists for the thread"
            )
        return existing

    async def update_last_event(
        self,
        *,
        link_id: UUID,
        last_event: dict[str, Any],
        last_message_id: str | None,
    ) -> AgentSurfaceConversationLink | None:
        model = await self.session.get(AgentSurfaceConversationLinkModel, link_id)
        if model is None:
            return None
        model.last_event = last_event
        model.last_message_id = last_message_id
        # Unconditional: this method exists to record an inbound event, and its
        # only caller is the ingress path. An outbound send that needs to repoint
        # a link uses ``repoint_conversation_for_outbound`` precisely so it can
        # never land here and fake inbound activity.
        model.last_inbound_at = datetime.now(timezone.utc)
        await self.session.flush()
        return model.to_entity()

    async def conversation_holds_notification(
        self,
        conversation_id: UUID,
        *,
        delivered_since: datetime | None = None,
    ) -> bool:
        """Is this conversation where a notification's answer is expected?

        Two things make it so. A notification that asked a question and is still
        open, however old: the recipient's agent is only told which request a
        reply answers (and given the id to record it against) through the
        conversation the request was delivered into, so a reply that lands
        anywhere else can never close it. And, when ``delivered_since`` is given,
        any notification delivered into it since then -- a report or a reminder
        the person answers with "yes" has to be read against the thing it
        answers.

        Read from the notification's own delivery columns rather than from the
        conversation's messages: the message is written before the send and the
        row afterwards, so only the row says the person was actually reached.
        Answered by ``ix_notifications_delivery_conversation``.
        """
        reasons = [
            and_(
                NotificationModel.status == NotificationStatus.OPEN.value,
                NotificationModel.expects_response.is_(True),
            )
        ]
        if delivered_since is not None:
            reasons.append(NotificationModel.delivered_at >= delivered_since)
        stmt = (
            select(NotificationModel.id)
            .where(
                NotificationModel.delivery_conversation_id == conversation_id,
                NotificationModel.delivery_status
                == NotificationDeliveryStatus.DELIVERED.value,
                or_(*reasons),
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).first() is not None

    async def repoint_conversation_for_outbound(
        self,
        *,
        link_id: UUID,
        conversation_id: UUID,
        expected_conversation_id: UUID,
        routed_agent_id: UUID | None = None,
    ) -> AgentSurfaceConversationLink | None:
        """Point a thread at a newly opened conversation, without faking inbound.

        Used when a notification opens a fresh conversation on a cold thread.
        Deliberately narrow next to ``update_conversation``: it leaves
        ``last_event``, ``last_message_id`` and ``last_inbound_at`` untouched, so
        the surface still knows when the person last spoke and the DM reset rule
        still works.

        Compare-and-set on ``expected_conversation_id``: an inbound arriving
        between our read and this write has already repointed the link, and
        stealing it back would split one thread across two conversations. Losing
        that race returns None and the caller delivers into the conversation the
        inbound created.

        ``routed_agent_id`` is the agent the new conversation was opened under.
        The link carries the agent its conversation belongs to, and the reply is
        routed to the surface's *current* agent: left at whatever the previous
        conversation had, the two disagree for a surface whose agent was changed
        since, and the reply is cut into a fresh conversation the notification
        never reached.
        """
        model = await self.session.get(AgentSurfaceConversationLinkModel, link_id)
        if model is None or model.conversation_id != expected_conversation_id:
            return None
        model.conversation_id = conversation_id
        if routed_agent_id is not None:
            model.routed_agent_id = routed_agent_id
        await self.session.flush()
        return model.to_entity()

    async def update_conversation(
        self,
        *,
        link_id: UUID,
        conversation_id: UUID,
        last_event: dict[str, Any],
        last_message_id: str | None,
        routed_agent_id: UUID | None = None,
        conversation_kind: str | None = None,
        route_key: str | None = None,
    ) -> AgentSurfaceConversationLink | None:
        model = await self.session.get(AgentSurfaceConversationLinkModel, link_id)
        if model is None:
            return None
        model.conversation_id = conversation_id
        model.last_event = last_event
        model.last_message_id = last_message_id
        model.routed_agent_id = routed_agent_id
        if conversation_kind is not None:
            model.conversation_kind = conversation_kind
        model.route_key = route_key
        # See ``update_last_event``: this is an inbound writer.
        model.last_inbound_at = datetime.now(timezone.utc)
        await self.session.flush()
        return model.to_entity()
