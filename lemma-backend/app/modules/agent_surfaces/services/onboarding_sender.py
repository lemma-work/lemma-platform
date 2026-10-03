"""Who is this sender, and is there anything to start for them?

Split from the coordinator because it is a different question from the one the
coordinator asks. The coordinator runs a conversation; this decides whether
there is a conversation to run -- whether the sender is already somebody, is
already talking to their own assistant, or is a stranger a pending signup has to
be opened for. The file was also at the architecture ratchet's per-file ceiling,
and this is the half that comes away cleanly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select

from app.core.helpers.identifiers import normalize_mobile_e164
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.composition import (
    build_conversation_binder,
    build_surface_router,
)
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
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
    has_somewhere_to_talk,
)
from app.modules.agent_surfaces.services.onboarding_transport import OnboardingTransport
from app.modules.agent_surfaces.services.personal_dm_routes import (
    PersonalRouteUnavailable,
    prepare_personal_dm_context,
)
from app.modules.agent_surfaces.services.verified_surface_identity import (
    phone_binding_holds,
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
            if verified_user is None or not phone_binding_holds(
                identity, verified_user, identity.platform
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
                    # The whole of `is_routable`, not the pod alone. The
                    # installation FK is SET NULL on purpose -- deleting a
                    # company's app leaves the proof of identity and clears the
                    # destination -- so a row with a pod and no installation is
                    # a state this query has to read as "no route", or it
                    # bypasses the workspace choice and fails validation later.
                    VerifiedSurfaceIdentity.installation_surface_id.is_not(None),
                    VerifiedSurfaceIdentity.revoked_at.is_(None),
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


async def personal_dm_result(
    event_dedup_store: SurfaceEventDedupStorePort,
    *,
    installation_surface_id: UUID,
    event: ParsedInboundSurfaceEvent,
    prepare: Callable[[], Awaitable[SurfaceChatContext]],
) -> OnboardingIngressResult:
    """A personal DM's context, under the delivery claim it takes for itself.

    This path answers instead of `prepare_ingress`, which is where the delivery
    claim otherwise lives. Without it a personal DM is the one conversation on
    the platform with no message-level defence against a redelivery -- and it is
    the one a person uses every day. Keyed on the route's own installation, which
    is what the context carries and therefore what `release_ingress_claim` hands
    back.
    """
    if not await event_dedup_store.claim_message(
        surface_installation_id=installation_surface_id,
        platform=event.platform.value,
        external_channel_id=event.external_channel_id,
        external_thread_id=event.external_thread_id,
        external_message_id=event.external_message_id,
    ):
        return OnboardingIngressResult(True)
    # Kept only once a context comes back. Any failure before that leaves the
    # claim spent with nothing behind it, so the inbox's retry would read the
    # message as a duplicate and drop it; a `finally` so a cancellation counts
    # too.
    prepared = False
    try:
        context = await prepare()
        prepared = True
    except PersonalRouteUnavailable:
        # The route died between one message and the next: the pod deleted, the
        # person removed from it, the app uninstalled. Not a failure to retry --
        # the path that will deliver it, ordinary ingestion, which routes by pod
        # membership, takes a claim of its own. Holding this one would make that
        # second attempt read as a duplicate and drop the message, which is why
        # the `finally` gives it back first.
        return OnboardingIngressResult(False)
    finally:
        if not prepared:
            await event_dedup_store.release_message(
                surface_installation_id=installation_surface_id,
                platform=event.platform.value,
                external_channel_id=event.external_channel_id,
                external_thread_id=event.external_thread_id,
                external_message_id=event.external_message_id,
            )
    return OnboardingIngressResult(True, context)


async def saved_default_outranks_route(
    uows: UnitOfWorkFactory, transport: OnboardingTransport, user_id: UUID
) -> bool:
    """Has this person chosen, through `/surfaces/me`, somewhere else to be answered?

    A personal route answers a private message before ordinary selection runs,
    and selection was the only place the saved default was ever read -- so the
    default, which routing documents as authoritative, did nothing for anyone
    with a route. Asked here, of the router's own predicate, the route steps
    aside exactly when selection would have honoured the default.
    """
    async with uows() as uow:
        default = await build_surface_router(uow).deliverable_default(
            user_id=user_id,
            parsed=transport.event,
            receiver_surface_ids=transport.receiver_surface_ids,
            # The same narrowing selection applies to an event that arrived on
            # the shared system bot.
            system_credentials_only=transport.surface is None,
        )
    return default is not None


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
        if await saved_default_outranks_route(uows, transport, verified_user_id):
            # Not handled here: ordinary ingestion selects by membership, the
            # saved default and continuity, which is the order the router
            # documents and the one the person asked for.
            return OnboardingIngressResult(False)
        route_id = route.id

        async def prepare() -> SurfaceChatContext:
            async with uows() as uow:
                return await prepare_personal_dm_context(
                    uow,
                    route_id=route_id,
                    event=event,
                    linker=build_conversation_binder(uow),
                )

        return await personal_dm_result(
            event_dedup_store,
            installation_surface_id=route.installation_surface_id,
            event=event,
            prepare=prepare,
        )
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
            # Recognised by a verified phone rather than by a binding, which is
            # a different route to the same place: someone the system knows,
            # who may still have nowhere to talk. Both paths ask it now, or
            # this one answers a WhatsApp-only user by telling them to open the
            # website -- the dead end the choice step exists to remove.
            offered = await offer_workspace_choice(
                uows, transport, resolved.internal_user_id
            )
            if offered is not None:
                return offered
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

    Returns the pending row on VERIFIED rather than sending anything, holding
    the message that got them here for replay. The coordinator settles it
    straight away -- already reachable, one pod, a list, or a new one -- through
    the same policy an email code ends in. What it must not do is walk into the
    workspace question with this message as the answer: that is how a first
    message of "2" attached somebody's second workspace, and "New York trip"
    came close to naming one.
    """
    event = transport.event
    if not event.is_dm:
        return None
    # Asked on both paths, because "no route stored on the identity" is not the
    # same as "nowhere to talk". Ordinary ingestion still routes an installation
    # message by pod membership, so a stored route missing is no reason to
    # interrupt anybody -- only the absence of any candidate is.
    async with uows() as uow:
        if await has_somewhere_to_talk(
            uow,
            user_id=verified_user_id,
            platform=event.platform,
            parsed=event,
            system_credentials_only=transport.surface is None,
            receiver_surface_ids=transport.receiver_surface_ids,
        ):
            return None
    state = await create_pending(uows, transport, event)
    async with uows() as uow:
        row = await uow.session.get(PendingChatOnboarding, state.id)
        assert row is not None
        row.step = OnboardingStep.VERIFIED
        row.user_id = verified_user_id
        row.destination = event.model_dump(mode="json")
    parked = await require_state(uows, transport.binding_key)
    assert parked is not None
    return parked
