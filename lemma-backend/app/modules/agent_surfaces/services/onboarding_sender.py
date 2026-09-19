"""Who is this sender, and is there anything to start for them?

Split from the coordinator because it is a different question from the one the
coordinator asks. The coordinator runs a conversation; this decides whether
there is a conversation to run -- whether the sender is already somebody, is
already talking to their own assistant, or is a stranger a pending signup has to
be opened for. The file was also at the architecture ratchet's per-file ceiling,
and this is the half that comes away cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select

from app.core.helpers.identifiers import normalize_mobile_e164
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.api.dependencies import get_surface_event_handler
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.onboarding_state import (
    OnboardingIngressResult,
    OnboardingStep,
    PendingState,
)
from app.modules.agent_surfaces.domain.ports import SurfaceEventDedupStorePort
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.services.identity_resolution_service import (
    SurfaceIdentityResolutionService,
)
from app.modules.agent_surfaces.services.onboarding_pod_choice import (
    candidate_pods,
    has_somewhere_to_talk,
)
from app.modules.agent_surfaces.services.onboarding_transport import OnboardingTransport
from app.modules.agent_surfaces.services.personal_dm_routes import (
    prepare_personal_dm_context,
)
from app.modules.identity.contracts.onboarding import (
    PENDING_TTL_SECONDS,
    active_chat_user,
)


@dataclass(frozen=True, slots=True)
class PersonalRoute:
    id: UUID
    installation_surface_id: UUID


def original_request(event: ParsedInboundSurfaceEvent) -> dict[str, JsonValue]:
    # Surrounding channel history and arbitrary platform payloads never enter
    # the personal assistant's conversation. Attachments retain provider IDs.
    return event.model_copy(
        update={
            "metadata": {"attachments": event.metadata.get("attachments", [])},
            "raw_payload": {},
        }
    ).model_dump(mode="json")


async def read_state(uows: UnitOfWorkFactory, binding_key: str) -> PendingState | None:
    async with uows() as uow:
        row = await uow.session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        return PendingState.model_validate(row) if row is not None else None


async def require_state(uows: UnitOfWorkFactory, binding_key: str) -> PendingState:
    state = await read_state(uows, binding_key)
    if state is None:
        raise ValueError("Pending onboarding disappeared")
    return state


async def verified_sender(
    uows: UnitOfWorkFactory, binding_key: str
) -> tuple[UUID | None, bool, PersonalRoute | None]:
    async with uows() as uow:
        identity = await uow.session.scalar(
            select(VerifiedSurfaceIdentity).where(
                VerifiedSurfaceIdentity.binding_key == binding_key
            )
        )
        verified_user_id = (
            identity.user_id
            if identity is not None and identity.revoked_at is None
            else None
        )
        previously_revoked = identity is not None and identity.revoked_at is not None
        if verified_user_id is not None:
            assert identity is not None
            verified_user = await active_chat_user(uow, verified_user_id)
            if verified_user is None or (
                identity.verified_phone is not None
                and (
                    identity.verified_phone != verified_user.mobile_number
                    or verified_user.mobile_verified_at is None
                )
            ):
                verified_user_id = None
                previously_revoked = True
        found = (
            await uow.session.execute(
                select(
                    VerifiedSurfaceIdentity.id,
                    VerifiedSurfaceIdentity.installation_surface_id,
                ).where(
                    VerifiedSurfaceIdentity.binding_key == binding_key,
                    VerifiedSurfaceIdentity.pod_id.is_not(None),
                )
            )
        ).first()
    route = PersonalRoute(*found) if found is not None else None
    return verified_user_id, previously_revoked, route


async def create_pending(
    uows: UnitOfWorkFactory,
    transport: OnboardingTransport,
    event: ParsedInboundSurfaceEvent,
) -> PendingState:
    async with uows() as uow:
        row = await uow.session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == transport.binding_key
            )
        )
        if row is not None:
            await uow.session.delete(row)
            await uow.session.flush()
        row = PendingChatOnboarding(
            binding_key=transport.binding_key,
            platform=event.platform.value,
            step=OnboardingStep.HANDOFF,
            destination={},
            original_event=original_request(event),
            installation_surface_id=transport.surface.id if transport.surface else None,
            expires_at=datetime.now(timezone.utc)
            + timedelta(
                seconds=min(
                    PENDING_TTL_SECONDS,
                    surface_settings.surface_onboarding_ttl_seconds,
                )
            ),
            verified_phone=normalize_mobile_e164(
                "+"
                + str(event.sender_phone or event.sender_external_user_id).lstrip("+")
            )
            if event.platform == SurfacePlatform.WHATSAPP
            else None,
        )
        uow.session.add(row)
    state = await require_state(uows, transport.binding_key)
    assert state is not None
    return state


async def recognize_sender(
    uows: UnitOfWorkFactory,
    transport: OnboardingTransport,
    *,
    adapters: SurfacePlatformAdapterRegistry,
    event_dedup_store: SurfaceEventDedupStorePort,
) -> PendingState | OnboardingIngressResult:
    event = transport.event
    verified_user_id, previously_revoked, route = await verified_sender(
        uows, transport.binding_key
    )
    if verified_user_id is not None and route is not None and event.is_dm:
        # This path answers instead of `prepare_ingress`, which is where the
        # delivery claim otherwise lives. Without it a personal DM is the one
        # conversation on the platform with no message-level defence against
        # a redelivery -- and it is the one a person uses every day. Keyed on
        # the route's own installation, which is what the context carries and
        # therefore what `release_ingress_claim` hands back.
        if not await event_dedup_store.claim_message(
            surface_installation_id=route.installation_surface_id,
            platform=event.platform.value,
            external_channel_id=event.external_channel_id,
            external_thread_id=event.external_thread_id,
            external_message_id=event.external_message_id,
        ):
            return OnboardingIngressResult(True)
        async with uows() as uow:
            context = await prepare_personal_dm_context(
                uow,
                route_id=route.id,
                event=event,
                linker=get_surface_event_handler(uow),
            )
        return OnboardingIngressResult(True, context)
    if verified_user_id is not None:
        offered = await offer_workspace_choice(uows, transport, verified_user_id)
        if offered is not None:
            return offered
        return OnboardingIngressResult(False)
    if not previously_revoked:
        adapter = adapters.get(event.platform)
        assert adapter is not None
        profile = await adapter.fetch_sender_profile(
            credentials=transport.credentials, event=event
        )
        async with uows() as uow:
            resolved = await SurfaceIdentityResolutionService(
                uow, ExternalSurfaceUserRepository(uow)
            ).resolve(event=event, sender_profile=profile, require_proven_identity=True)
        if resolved.internal_user_id is not None:
            return OnboardingIngressResult(False)
        if profile is not None:
            event = event.model_copy(
                update={
                    "sender_email": profile.email,
                    "sender_display_name": profile.display_name
                    or event.sender_display_name,
                }
            )
    return await create_pending(uows, transport, event)


async def offer_workspace_choice(
    uows: UnitOfWorkFactory,
    transport: OnboardingTransport,
    verified_user_id: UUID,
) -> PendingState | None:
    """Park a recognised sender on "which workspace?" instead of a dead end.

    Only for a private chat on an installation that routes personally: there,
    "recognised but no route" means there is genuinely nowhere for the message
    to go, and ordinary ingestion would answer someone who already has an
    account by telling them to go and use the website. Everywhere else -- a
    shared surface, a group -- routing is not this person's to choose, so this
    declines by returning None and the caller carries on as before.

    Returns the parked state rather than sending anything: the dispatcher walks
    straight into the AWAITING_POD step, which asks the question through the
    same reply path every other step uses, and the message that got them here
    is kept for replay once they answer.
    """
    event = transport.event
    if not event.is_dm:
        return None
    if transport.surface is None:
        # The shared bot. Here a destination is not stored on the identity --
        # routing works it out per message from the pods this person is in,
        # picking by saved default, then continuity, then a tiebreak. So the
        # question is not "is there a route" but "is there anything to choose
        # among at all": asking otherwise would interrupt every working person
        # on the busiest path in the product, every message.
        async with uows() as uow:
            if await has_somewhere_to_talk(
                uow, user_id=verified_user_id, platform=event.platform
            ):
                return None
    async with uows() as uow:
        pods = await candidate_pods(uow, user_id=verified_user_id)
    state = await create_pending(uows, transport, event)
    async with uows() as uow:
        row = await uow.session.get(PendingChatOnboarding, state.id)
        assert row is not None
        row.step = OnboardingStep.AWAITING_POD
        row.user_id = verified_user_id
        row.offered_pods = pods
        row.destination = event.model_dump(mode="json")
    parked = await require_state(uows, transport.binding_key)
    assert parked is not None
    return parked
