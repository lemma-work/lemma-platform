"""Private, resumable signup before surface routing and conversation ingestion."""

from __future__ import annotations

from uuid import UUID
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import SurfacePlatform

from datetime import datetime, timezone

from sqlalchemy import select

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceCredentialMode,
    AgentSurfaceStatus,
)
from app.modules.agent_surfaces.domain.events import SurfaceOnboardingReadyEvent
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
    PersonalDMRoute,
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.services.onboarding_transport import (
    OnboardingTransport,
)
from app.modules.identity.contracts.onboarding import (
    ChallengeRejected,
    active_chat_user,
    ensure_chat_workspace,
)
from app.modules.identity.contracts.surfaces import (
    set_user_preferences,
    user_preferences,
)


from app.modules.agent_surfaces.domain.onboarding_state import PendingState


async def complete_onboarding_workspace(
    uows: UnitOfWorkFactory, transport: OnboardingTransport, state: PendingState
) -> bool:
    assert state.user_id is not None
    workspace = await ensure_chat_workspace(
        uows,
        user_id=state.user_id,
        verified_phone=state.verified_phone,
        full_name=transport.event.sender_display_name,
        installation_organization_id=transport.organization_id,
    )
    async with uows() as uow:
        user = await active_chat_user(uow, state.user_id)
        if user is None:
            raise ChallengeRejected("This account cannot chat")
        identity = await uow.session.scalar(
            select(VerifiedSurfaceIdentity).where(
                VerifiedSurfaceIdentity.binding_key == state.binding_key
            )
        )
        if identity is not None and identity.user_id != user.id:
            raise ChallengeRejected("This platform identity belongs to another account")
        if identity is None:
            identity = VerifiedSurfaceIdentity(
                binding_key=state.binding_key,
                platform=transport.event.platform.value,
                tenant_id=transport.event.tenant_id or "",
                external_user_id=transport.event.sender_external_user_id or "",
                user_id=user.id,
            )
            uow.session.add(identity)
        identity.verified_phone = state.verified_phone
        identity.revoked_at = None
        pending = await uow.session.get(PendingChatOnboarding, state.id)
        assert pending is not None
        if workspace.status == "organization_access_required":
            pending.step = "organization_access_required"
        else:
            assert workspace.pod_id is not None and workspace.assistant_id is not None
            if transport.surface is not None:
                route = await uow.session.scalar(
                    select(PersonalDMRoute).where(
                        PersonalDMRoute.binding_key == state.binding_key
                    )
                )
                if route is None:
                    route = PersonalDMRoute(
                        binding_key=state.binding_key,
                        installation_surface_id=transport.surface.id,
                        user_id=user.id,
                        pod_id=workspace.pod_id,
                        assistant_id=workspace.assistant_id,
                    )
                    uow.session.add(route)
                else:
                    route.pod_id, route.assistant_id = (
                        workspace.pod_id,
                        workspace.assistant_id,
                    )
            else:
                await _ensure_shared_surface(
                    uow,
                    pod_id=workspace.pod_id,
                    assistant_id=workspace.assistant_id,
                    user_id=user.id,
                    platform=transport.event.platform,
                )
            pending.step = "ready"
            if pending.ready_at is None:
                pending.ready_at = datetime.now(timezone.utc)
                uow.collect_events([SurfaceOnboardingReadyEvent(pending_id=pending.id)])
    return workspace.status == "organization_access_required"


async def _ensure_shared_surface(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    assistant_id: UUID,
    user_id: UUID,
    platform: SurfacePlatform,
) -> None:
    repository = SurfaceRepository(uow)
    surfaces, _ = await repository.list_by_pod(pod_id, platform=platform.value)
    surface = next(
        (
            item
            for item in surfaces
            if item.account_id is None
            and item.credential_mode == SurfaceCredentialMode.SYSTEM
            and item.agent_id == assistant_id
        ),
        None,
    )
    if surface is None:
        surface = await repository.create(
            AgentSurfaceEntity.create(
                pod_id=pod_id,
                surface_type=platform,
                agent_id=assistant_id,
                name=f"lemma-{platform.value.lower()}-{str(assistant_id)[:8]}",
            )
        )
    if not surface.is_active or surface.status != AgentSurfaceStatus.ACTIVE:
        surface.activate()
        surface = await repository.update(surface)
    preferences = await user_preferences(uow, user_id)
    await set_user_preferences(
        uow,
        user_id,
        preferences.with_default_surface(platform.value, surface.id),
    )
