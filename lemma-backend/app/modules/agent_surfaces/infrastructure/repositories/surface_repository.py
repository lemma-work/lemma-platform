from __future__ import annotations

from collections.abc import Collection
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.crypto import get_secret_cipher
from app.core.domain.uow import IUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    AgentSurfaceStatus,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.domain.ports import (
    SurfaceInstallationRepositoryPort,
)
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurface,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_routing_sql import (
    active_surfaces_of_type,
    in_a_live_pod,
    routing_surfaces,
)
from app.modules.pod.contracts.orm import Pod
from app.modules.agent.contracts.conversations import (
    merge_conversation_metadata as merge_agent_conversation_metadata,
)


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
        self, conversation_id: UUID, updates: dict[str, object]
    ) -> None:
        """Merge ``updates`` into a conversation's metadata blob (no-op if gone)."""
        await merge_agent_conversation_metadata(self.uow, conversation_id, updates)

    async def get(self, id: UUID) -> AgentSurfaceEntity | None:
        model = await self.session.get(AgentSurface, id)
        return model.to_entity_or_none() if model else None

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
        return model.to_entity_or_none() if model else None

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

        # A row naming a retired platform drops out rather than taking the
        # whole page with it; see `AgentSurface.to_entity_or_none`.
        entities = [
            entity
            for entity in (model.to_entity_or_none() for model in models)
            if entity is not None
        ]
        return entities, next_cursor

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
        """Every live surface of one platform. **No production caller, by design.**

        This was the read every routing path started from, and narrowing them
        left it with no caller and no place on the port -- an unnarrowed read is
        not something a routing path should be able to reach for. It stays here
        as the oracle the equivalence tests compare against: they filter this in
        Python and assert `list_active_for_routing` returns the same rows.
        """
        result = await self.session.execute(active_surfaces_of_type(surface_type))
        return [model.to_entity() for model in result.scalars().all()]

    async def list_active_for_routing(
        self,
        surface_type: str,
        *,
        surface_ids: Collection[UUID] | None = None,
        pod_ids: Collection[UUID] | None = None,
        external_workspace_id: str | None = None,
        system_credentials_only: bool = False,
    ) -> list[AgentSurfaceEntity]:
        """The live surfaces an inbound event could be for; see `routing_surfaces`."""
        result = await self.session.execute(
            routing_surfaces(
                surface_type,
                surface_ids=surface_ids,
                pod_ids=pod_ids,
                external_workspace_id=external_workspace_id,
                system_credentials_only=system_credentials_only,
            )
        )
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
            .join(Pod, in_a_live_pod())
            .order_by(AgentSurface.surface_type, AgentSurface.id)
        )
        result = await self.session.execute(stmt)
        return [model.to_entity() for model in result.scalars().all()]

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
        # Org-wide and platform-blind, so a retired row sharing this account
        # would otherwise 500 the creation of an unrelated surface.
        return model.to_entity_or_none() if model else None

    async def _organization_for_pod(self, pod_id: UUID) -> UUID:
        """The organisation this surface is in, read from the pod that defines it.

        Carried on the row rather than joined for, because per-organisation
        uniqueness of a pooled WhatsApp number has to be expressible as an
        index, and an index cannot reach through a join.

        Read here rather than taken from the entity, and that is the whole
        reason it is not on `AgentSurfaceEntity`: a denormalised column a caller
        can set is a denormalised column a caller can set wrongly. The composite
        foreign key would catch it, but it would catch it as a constraint
        violation naming `pods`, which tells whoever is reading the traceback
        nothing about which caller was confused. One reader, one definition.

        `update` needs no equivalent: it never moves a surface between pods, and
        a pod that changes organisation drags its surfaces along through
        `ON UPDATE CASCADE` without anything here running.
        """
        organization_id = await self.session.scalar(
            select(Pod.organization_id).where(Pod.id == pod_id)
        )
        if organization_id is None:
            raise AgentSurfaceValidationError(
                f"Cannot create a surface for pod {pod_id}: it does not exist, "
                "so there is no organization to scope it to"
            )
        return organization_id

    async def create(self, entity: AgentSurfaceEntity) -> AgentSurfaceEntity:
        model = AgentSurface(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            pod_id=entity.pod_id,
            organization_id=await self._organization_for_pod(entity.pod_id),
            name=entity.name,
            agent_id=entity.agent_id,
            surface_type=entity.surface_type.value,
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
