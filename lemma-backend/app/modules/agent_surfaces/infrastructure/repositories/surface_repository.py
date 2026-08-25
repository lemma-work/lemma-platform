from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.crypto import get_secret_cipher
from app.core.domain.uow import IUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    AgentSurfaceStatus,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceInstallationRepositoryPort,
)
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurface,
    AgentSurfaceConversationLinkModel,
)
from app.composition.surface_identity import Pod
from app.composition.surface_agent import ConversationModel


#: A surface belongs to a pod, and a deleted pod has no business answering on
#: it. `PS-OPS-020` says deleting a pod stops the work it was doing and keeps it
#: stopped -- and a surface is the one piece of standing work that keeps running
#: without anybody in Lemma asking it to, because the trigger comes from
#: outside. The surface row itself stays ACTIVE on purpose: deletion is soft, so
#: nothing is rewritten and an undelete would restore a working surface. What
#: changes is that the pod is joined and checked here, once, rather than by each
#: of the ingress paths remembering to.
def _in_a_live_pod():
    return (Pod.id == AgentSurface.pod_id) & (Pod.is_deleted.is_(False))


class SurfaceRepository(SurfaceInstallationRepositoryPort):
    """Repository for agent surface installations."""

    def __init__(self, uow: IUnitOfWork, message_bus: Any = None):
        self.uow = uow
        self.session: Session = uow.session
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    def _collect_events(self, entity: AgentSurfaceEntity) -> None:
        events = entity.collect_events()
        if events:
            self.uow.collect_events(events)

    async def merge_conversation_metadata(
        self, conversation_id: UUID, updates: dict
    ) -> None:
        """Merge ``updates`` into a conversation's metadata blob (no-op if gone)."""
        model = await self.session.get(ConversationModel, conversation_id)
        if model is None:
            return
        metadata = dict(model.conversation_metadata or {})
        metadata.update(updates)
        model.conversation_metadata = metadata
        await self.session.flush()

    async def get(self, id: UUID) -> AgentSurfaceEntity | None:
        model = await self.session.get(AgentSurface, id)
        return model.to_entity() if model else None

    async def get_by_pod_and_name(
        self,
        *,
        pod_id: UUID,
        name: str,
    ) -> AgentSurfaceEntity | None:
        """Resolve a surface by its stable, pod-unique name (the API identity)."""
        stmt = select(AgentSurface).where(
            AgentSurface.pod_id == pod_id,
            AgentSurface.name == name,
        )
        result = await self.session.execute(stmt)
        model = result.scalars().first()
        return model.to_entity() if model else None

    async def list_by_pod(
        self,
        pod_id: UUID,
        *,
        platform: str | None = None,
        agent_id: UUID | None = None,
        match_agent: bool = False,
        cursor: UUID | None = None,
        limit: int = 100,
    ) -> tuple[list[AgentSurfaceEntity], UUID | None]:
        stmt = select(AgentSurface).where(AgentSurface.pod_id == pod_id)
        if platform:
            stmt = stmt.where(AgentSurface.surface_type == str(platform).upper())
        if match_agent:
            stmt = stmt.where(AgentSurface.agent_id == agent_id)
        if cursor is not None:
            stmt = stmt.where(AgentSurface.id > cursor)
        stmt = stmt.order_by(AgentSurface.id).limit(limit + 1)
        result = await self.session.execute(stmt)
        models = list(result.scalars().all())

        next_cursor = None
        if len(models) > limit:
            next_cursor = models[limit - 1].id
            models = models[:limit]

        return [model.to_entity() for model in models], next_cursor

    async def get_active_by_address(
        self,
        *,
        platform: str,
        address: str,
    ) -> AgentSurfaceEntity | None:
        """Active surface whose provisioned email address matches (e.g. Resend)."""
        stmt = (
            select(AgentSurface)
            .where(
                AgentSurface.surface_type == str(platform).upper(),
                func.lower(AgentSurface.surface_identity_email)
                == address.strip().lower(),
                AgentSurface.status == AgentSurfaceStatus.ACTIVE.value,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        model = result.scalars().first()
        return model.to_entity() if model else None

    async def list_active_by_type(self, surface_type: str) -> list[AgentSurfaceEntity]:
        # Stable ordering so surface selection is deterministic when a sender
        # resolves to several candidate surfaces on a shared bot/number (the
        # ingress tiebreak relies on this order).
        stmt = (
            select(AgentSurface)
            .where(
                AgentSurface.surface_type == surface_type,
                AgentSurface.status == AgentSurfaceStatus.ACTIVE.value,
            )
            .join(Pod, _in_a_live_pod())
            .order_by(AgentSurface.created_at, AgentSurface.id)
        )
        result = await self.session.execute(stmt)
        return [model.to_entity() for model in result.scalars().all()]

    async def list_active_native_receiver_surfaces(
        self,
        platforms: set[SurfacePlatform],
    ) -> list[AgentSurfaceEntity]:
        if not platforms:
            return []
        stmt = (
            select(AgentSurface)
            .where(
                AgentSurface.surface_type.in_(
                    [platform.value for platform in platforms]
                ),
                AgentSurface.status == AgentSurfaceStatus.ACTIVE.value,
            )
            .join(Pod, _in_a_live_pod())
            .order_by(AgentSurface.surface_type, AgentSurface.id)
        )
        result = await self.session.execute(stmt)
        return [model.to_entity() for model in result.scalars().all()]

    async def get_by_email_schedule_id(
        self, schedule_id: UUID
    ) -> AgentSurfaceEntity | None:
        stmt = select(AgentSurface).where(AgentSurface.schedule_id == schedule_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def get_by_platform_and_account_id(
        self,
        *,
        platform: str,
        account_id: UUID,
        exclude_surface_id: UUID | None = None,
    ) -> AgentSurfaceEntity | None:
        stmt = select(AgentSurface).where(
            AgentSurface.surface_type == platform,
            AgentSurface.account_id == account_id,
        )
        if exclude_surface_id is not None:
            stmt = stmt.where(AgentSurface.id != exclude_surface_id)
        stmt = stmt.limit(1)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def get_system_credential_conflict_in_org(
        self,
        *,
        pod_id: UUID,
        platform: str,
        exclude_surface_id: UUID | None = None,
    ) -> AgentSurfaceEntity | None:
        target_org_id = (
            select(Pod.organization_id).where(Pod.id == pod_id).scalar_subquery()
        )
        stmt = (
            select(AgentSurface)
            .join(Pod, Pod.id == AgentSurface.pod_id)
            .where(
                Pod.organization_id == target_org_id,
                AgentSurface.surface_type == str(platform).upper(),
                AgentSurface.credential_mode == "SYSTEM",
                AgentSurface.account_id.is_(None),
            )
            .limit(1)
        )
        if exclude_surface_id is not None:
            stmt = stmt.where(AgentSurface.id != exclude_surface_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def get_account_conflict_in_org(
        self,
        *,
        pod_id: UUID,
        account_id: UUID,
        exclude_surface_id: UUID | None = None,
    ) -> AgentSurfaceEntity | None:
        target_org_id = (
            select(Pod.organization_id).where(Pod.id == pod_id).scalar_subquery()
        )
        stmt = (
            select(AgentSurface)
            .join(Pod, Pod.id == AgentSurface.pod_id)
            .where(
                Pod.organization_id == target_org_id,
                AgentSurface.account_id == account_id,
            )
            .limit(1)
        )
        if exclude_surface_id is not None:
            stmt = stmt.where(AgentSurface.id != exclude_surface_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def create(self, entity: AgentSurfaceEntity) -> AgentSurfaceEntity:
        model = AgentSurface(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            pod_id=entity.pod_id,
            name=entity.name,
            agent_id=entity.agent_id,
            surface_type=entity.surface_type.value,
            mode=entity.mode.value
            if hasattr(entity.mode, "value")
            else str(entity.mode),
            event_mode=(
                entity.event_mode.value
                if hasattr(entity.event_mode, "value")
                else str(entity.event_mode)
            ),
            credential_mode=(
                entity.credential_mode.value
                if hasattr(entity.credential_mode, "value")
                else str(entity.credential_mode)
            ),
            config=entity.config.model_dump(mode="json"),
            account_id=entity.account_id,
            external_workspace_id=entity.external_workspace_id,
            external_tenant_id=entity.external_tenant_id,
            external_channel_id=entity.external_channel_id,
            surface_identity_id=entity.surface_identity_id,
            surface_identity_username=entity.surface_identity_username,
            status=entity.status.value,
            schedule_id=entity.schedule_id,
            surface_identity_email=entity.surface_identity_email,
            webhook_secret=get_secret_cipher().encrypt_str(entity.webhook_secret),
        )
        self.session.add(model)
        await self.session.flush()
        self._collect_events(entity)
        return model.to_entity()

    async def update(self, entity: AgentSurfaceEntity) -> AgentSurfaceEntity:
        model = await self.session.get(AgentSurface, entity.id)
        if model is None:
            return entity
        model.updated_at = entity.updated_at
        model.agent_id = entity.agent_id
        model.surface_type = entity.surface_type.value
        model.mode = (
            entity.mode.value if hasattr(entity.mode, "value") else str(entity.mode)
        )
        model.event_mode = (
            entity.event_mode.value
            if hasattr(entity.event_mode, "value")
            else str(entity.event_mode)
        )
        model.credential_mode = (
            entity.credential_mode.value
            if hasattr(entity.credential_mode, "value")
            else str(entity.credential_mode)
        )
        model.config = entity.config.model_dump(mode="json")
        model.account_id = entity.account_id
        model.external_workspace_id = entity.external_workspace_id
        model.external_tenant_id = entity.external_tenant_id
        model.external_channel_id = entity.external_channel_id
        model.surface_identity_id = entity.surface_identity_id
        model.surface_identity_username = entity.surface_identity_username
        model.status = entity.status.value
        model.schedule_id = entity.schedule_id
        model.surface_identity_email = entity.surface_identity_email
        model.webhook_secret = get_secret_cipher().encrypt_str(entity.webhook_secret)
        await self.session.flush()
        self._collect_events(entity)
        return entity

    async def delete(self, id: UUID) -> None:
        model = await self.session.get(AgentSurface, id)
        if model is None:
            return
        await self.session.delete(model)
        await self.session.flush()


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
    ) -> UUID | None:
        """The surface an existing conversation for this exact chat lives on.

        Same match shape as ``get_by_external_thread`` but NOT scoped to a
        surface id — used at ingress to keep a returning chat on the surface it
        first landed on, so a sender reachable via a shared bot across several
        pods doesn't bounce between them. Returns the most-recently-updated
        link's surface id, or None when the chat is new.
        """
        stmt = select(AgentSurfaceConversationLinkModel.surface_id).where(
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

        Ordered by inbound recency, not ``updated_at``: an outbound message also
        bumps ``updated_at``, so ranking by it would mean "the thread we last
        talked *at* them on" rather than "the thread they last talked to us on".
        Only the second is evidence of where they are actually looking. COALESCE
        keeps pre-migration rows, where the two were the same thing, in the sort.
        """
        stmt = (
            select(AgentSurfaceConversationLinkModel)
            .where(
                AgentSurfaceConversationLinkModel.surface_id == surface_id,
                AgentSurfaceConversationLinkModel.external_user_id == external_user_id,
            )
            .order_by(
                func.coalesce(
                    AgentSurfaceConversationLinkModel.last_inbound_at,
                    AgentSurfaceConversationLinkModel.updated_at,
                ).desc()
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        model = result.scalars().first()
        return model.to_entity() if model else None

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
        last_event: dict,
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
        last_event: dict,
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
