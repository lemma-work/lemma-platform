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

from collections.abc import Collection, Sequence
from typing import Any
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.domain.uow import IUnitOfWork
from app.modules.agent_surfaces.domain.entities import AgentSurfaceConversationLink
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurfaceConversationLinkModel,
)


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

    async def create(
        self,
        link: AgentSurfaceConversationLink,
    ) -> AgentSurfaceConversationLink:
        model = AgentSurfaceConversationLinkModel(
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
        self.session.add(model)
        await self.session.flush()
        return model.to_entity()

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

    async def repoint_conversation_for_outbound(
        self,
        *,
        link_id: UUID,
        conversation_id: UUID,
        expected_conversation_id: UUID,
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
        """
        model = await self.session.get(AgentSurfaceConversationLinkModel, link_id)
        if model is None or model.conversation_id != expected_conversation_id:
            return None
        model.conversation_id = conversation_id
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
