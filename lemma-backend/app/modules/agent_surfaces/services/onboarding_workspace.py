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
    ensure_chat_organization,
    ensure_chat_workspace,
)
from app.modules.agent.contracts.provisioning import ensure_pod_default_agent
from app.modules.agent_surfaces.services.onboarding_pod_choice import (
    PodChoice,
    candidate_pods,
    offer_text,
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


class SharedSurfaceUnavailable(ChallengeRejected):
    """This workspace cannot carry the shared bot, but another one might.

    Separate from every other refusal because it is the only one with a next
    step the person can take, and saying "pick another workspace" is not the
    same as letting them. Carries the pod that refused, so the list offered
    next does not lead with it.
    """

    def __init__(self, message: str, *, pod_id: UUID) -> None:
        super().__init__(message)
        self.pod_id = pod_id


async def complete_onboarding_workspace(
    uows: UnitOfWorkFactory, transport: OnboardingTransport, state: PendingState
) -> bool:
    assert state.user_id is not None
    try:
        return await _provision_and_bind(uows, transport, state)
    except SharedSurfaceUnavailable as conflict:
        # Raised from inside a unit of work, so the park has to happen after it
        # has rolled back -- and `_step` turns what comes out of here into the
        # reply, so the offer travels on the message.
        raise ChallengeRejected(
            await _park_on_another_workspace(uows, transport, state, conflict)
        ) from conflict


async def _park_on_another_workspace(
    uows: UnitOfWorkFactory,
    transport: OnboardingTransport,
    state: PendingState,
    conflict: SharedSurfaceUnavailable,
) -> str:
    """Move a stuck signup onto the workspace question, and ask it.

    Without this the refusal was a dead end wearing an instruction: the step
    stayed on VERIFIED, so the next message -- `new Personal`, or anything --
    re-ran provisioning against the same pod and was refused again in the same
    words. Nothing was reading the answer, because nothing had asked a question.

    The pod that just refused is left off the list. It is still re-checked if
    they name it some other way, but offering it back is offering the failure.
    """
    assert state.user_id is not None
    async with uows() as uow:
        pods = [
            pod
            for pod in await candidate_pods(
                uow,
                user_id=state.user_id,
                organization_id=transport.organization_id,
            )
            if UUID(str(pod["id"])) != conflict.pod_id
        ]
    async with uows() as uow:
        row = await uow.session.get(PendingChatOnboarding, state.id)
        assert row is not None
        row.step = OnboardingStep.AWAITING_POD
        row.user_id = state.user_id
        row.offered_pods = pods
    return f"{conflict.message}\n\n{offer_text(pods)}"


async def _provision_and_bind(
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
                # The destination goes on the identity, which is the row
                # `identity` already is: one write, and nothing that can
                # outlive a revocation.
                identity.installation_surface_id = transport.surface.id
                identity.pod_id = workspace.pod_id
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
        # An agent reaches a platform in one place, so if this one already has
        # a surface here that the lookup above did not match -- a bot on the
        # company's own credentials -- there is no second place to put the
        # shared one. Creating it anyway is what `uq_agent_surface_agent_type`
        # refuses, and an IntegrityError is a poor way to tell someone their
        # workspace is already reachable another way.
        #
        # Nor can the existing one simply be reused: shared routing considers
        # system-credential surfaces only, so a default pointing at a custom
        # bot is a default that is always ignored.
        if any(item.agent_id == assistant_id for item in surfaces):
            raise SharedSurfaceUnavailable(
                "That workspace's assistant already answers on "
                f"{platform.value.title()} through its own connection. "
                "Pick another workspace, or message it there.",
                pod_id=pod_id,
            )
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


async def _organization_for(
    uow: SqlAlchemyUnitOfWork,
    *,
    user,
    transport: OnboardingTransport,
    placement: tuple[UUID, UUID] | None,
) -> UUID | None:
    """Where a newly named workspace goes, provisioning one where that is allowed.

    Two different nothings hid behind one refusal. Somebody outside a company's
    installation genuinely has to be added by an administrator -- that boundary
    is the point of the installation. Somebody arriving on the *shared* bot with
    a personal address and no organization at all was told the same thing, and
    there was no administrator to ask: the message sent a verified person to a
    door that does not exist. For them this runs the ordinary first-workspace
    policy, which is what the web signup would have done a minute earlier.
    """
    if placement is not None:
        return placement[0]
    if transport.organization_id is not None:
        return None
    return await ensure_chat_organization(uow, user_id=user.id)


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
            organization_id = await _organization_for(
                uow, user=user, transport=transport, placement=placement
            )
            if organization_id is None:
                return (
                    "That account is not in any Lemma organization yet, so there "
                    "is nowhere to put a workspace. Ask your admin to add you."
                )
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
            allowed = await candidate_pods(
                uow,
                user_id=user.id,
                organization_id=transport.organization_id,
                limit=None,
            )
            if not any(UUID(str(item["id"])) == pod_id for item in allowed):
                return "That workspace is no longer available. Pick another."
        assistant_id = await ensure_pod_default_agent(
            uow, pod_id=pod_id, user_id=user.id
        )
        if transport.surface is not None:
            identity = await uow.session.scalar(
                select(VerifiedSurfaceIdentity).where(
                    VerifiedSurfaceIdentity.binding_key == state.binding_key,
                    VerifiedSurfaceIdentity.revoked_at.is_(None),
                )
            )
            if identity is None:
                return "That account needs to verify again before it can chat."
            identity.installation_surface_id = transport.surface.id
            identity.pod_id = pod_id
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
