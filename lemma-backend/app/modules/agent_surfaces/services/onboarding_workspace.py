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
from app.modules.agent.contracts.provisioning import ensure_pod_default_agent
from app.modules.agent_surfaces.services.onboarding_pod_choice import (
    PodChoice,
    candidate_pods,
    organization_for_new_pod,
)
from app.modules.identity.contracts.surfaces import (
    set_user_preferences,
    user_preferences,
)
from app.modules.pod.contracts.personal_workspace import create_named_workspace


from app.modules.agent_surfaces.domain.onboarding_state import (
    OnboardingStep,
    PendingState,
)


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
            pending.step = OnboardingStep.ORGANIZATION_ACCESS_REQUIRED
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
            pending.step = OnboardingStep.READY
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


async def attach_chosen_workspace(
    uows: UnitOfWorkFactory,
    transport: OnboardingTransport,
    state: PendingState,
    choice: PodChoice,
) -> str | None:
    """Wire this conversation to the workspace the person picked.

    The sibling of `complete_onboarding_workspace`, for someone who already had
    an account: no organization is provisioned and no pod is invented, because
    they chose. Returns a message to send back when the choice could not be
    honoured, and None when it was.
    """
    assert state.user_id is not None
    async with uows() as uow:
        user = await active_chat_user(uow, state.user_id)
        if user is None:
            raise ChallengeRejected("This account cannot chat")
        pod_id = choice.pod_id
        if pod_id is None:
            assert choice.new_name is not None
            placement = await organization_for_new_pod(
                uow,
                user_id=user.id,
                installation_organization_id=transport.organization_id,
            )
            if placement is None:
                return (
                    "That account is not in any Lemma organization yet, so there "
                    "is nowhere to put a workspace. Ask your admin to add you."
                )
            organization_id, _membership_id = placement
            made = await create_named_workspace(
                uow,
                organization_id=organization_id,
                owner_user_id=user.id,
                name=choice.new_name,
            )
            pod_id = made.pod_id
        else:
            # Re-proving membership rather than trusting the stored list: the
            # offer was written when the question was asked, and access can be
            # taken away between a question and its answer.
            allowed = await candidate_pods(uow, user_id=user.id, limit=None)
            if not any(UUID(str(item["id"])) == pod_id for item in allowed):
                return "That workspace is no longer available. Pick another."
        assistant_id = await ensure_pod_default_agent(
            uow, pod_id=pod_id, user_id=user.id
        )
        if transport.surface is not None:
            route = await uow.session.scalar(
                select(PersonalDMRoute).where(
                    PersonalDMRoute.binding_key == state.binding_key
                )
            )
            if route is None:
                uow.session.add(
                    PersonalDMRoute(
                        binding_key=state.binding_key,
                        installation_surface_id=transport.surface.id,
                        user_id=user.id,
                        pod_id=pod_id,
                        assistant_id=assistant_id,
                    )
                )
            else:
                route.pod_id, route.assistant_id = pod_id, assistant_id
        else:
            await _ensure_shared_surface(
                uow,
                pod_id=pod_id,
                assistant_id=assistant_id,
                user_id=user.id,
                platform=transport.event.platform,
            )
        pending = await uow.session.get(PendingChatOnboarding, state.id)
        assert pending is not None
        pending.step = OnboardingStep.READY
        pending.offered_pods = None
        if pending.ready_at is None:
            pending.ready_at = datetime.now(timezone.utc)
            uow.collect_events([SurfaceOnboardingReadyEvent(pending_id=pending.id)])
    return None
