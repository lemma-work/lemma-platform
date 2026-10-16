"""Hand off the saved user request through the normal ingestion/run pipeline.

The onboarding's "ready" transaction writes a ``surface.ingest`` item naming
the pending row; that item prepares the context here and starts the turn, and
marks the row handed off in the transaction that writes the message.
"""

from __future__ import annotations

from app.modules.agent_surfaces.infrastructure.repositories.verified_surface_identity_repository import (
    VerifiedSurfaceIdentityRepository,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.identity.contracts.onboarding import UserEntity
from app.modules.agent_surfaces.domain.ingress_context import (
    AgentSurfaceContext,
    SurfaceChatContext,
)

from datetime import datetime, timezone
from uuid import UUID


from app.modules.agent_surfaces.composition import (
    build_conversation_binder,
    build_surface_ingress,
)
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.domain.onboarding_state import PendingState
from app.modules.agent_surfaces.services.personal_dm_routes import (
    prepare_personal_dm_context,
)
from app.modules.identity.contracts.onboarding import active_chat_user


async def prepare_onboarding_replay(
    uow: SqlAlchemyUnitOfWork, pending_id: UUID
) -> AgentSurfaceContext | None:
    """The context a ready onboarding hands to the ordinary run pipeline.

    ``None`` when there is nothing (left) to hand off: the row is gone, not
    ready, or already handed off -- which is what makes a retried
    ``surface.ingest`` for it a no-op.
    """
    row = await uow.session.get(PendingChatOnboarding, pending_id)
    if row is None or row.ready_at is None or row.handed_off_at is not None:
        return None
    state = PendingState.model_validate(row)
    if state.user_id is None:
        raise ValueError("Onboarding has no verified account")
    user = await active_chat_user(uow, state.user_id)
    if user is None:
        raise ValueError("The onboarding account is no longer active")
    destination = ParsedInboundSurfaceEvent.model_validate(state.destination)
    if not destination.is_dm:
        raise ValueError("Onboarding replay requires a private destination")
    if state.original_event is None or state.expires_at <= datetime.now(timezone.utc):
        return await _expired_reply_context(uow, state, destination)
    original = ParsedInboundSurfaceEvent.model_validate(state.original_event)
    event = original.model_copy(
        update={
            "is_dm": True,
            "conversation_type": destination.conversation_type,
            "external_channel_id": destination.external_channel_id,
            "external_thread_id": destination.external_thread_id,
            "reply_target": destination.reply_target,
            "external_message_id": f"onboarding:{state.id}",
            "raw_payload": {},
        }
    )
    if state.installation_surface_id is not None:
        # The whole of `is_routable`, as `verified_sender` asks it: on the pod
        # alone, a row whose installation was cleared would be refused inside
        # `validate_personal_dm_route`, a raise in the middle of a replay.
        route = await VerifiedSurfaceIdentityRepository(uow).personal_route(
            state.binding_key
        )
        route_id = route[0] if route is not None else None
        if route_id is None:
            raise ValueError("The personal DM route is missing")
        context: AgentSurfaceContext = await prepare_personal_dm_context(
            uow,
            route_id=route_id,
            event=event,
            linker=build_conversation_binder(uow),
        )
    else:
        context = await _shared_replay_context(uow, state, event, user)
    if isinstance(context, SurfaceChatContext):
        context = context.model_copy(update={"onboarding_handoff_id": state.id})
    return context


async def hand_off_onboarding(
    uow_factory, pending_id: UUID
) -> AgentSurfaceContext | None:
    """Prepare the replay and mark the row handed off, in one transaction.

    What ``surface.ingest`` does around the turn it then starts, without the
    turn: for a caller (a test, an operator) that starts it itself.
    """
    async with uow_factory() as uow:
        context = await prepare_onboarding_replay(uow, pending_id)
        if context is not None:
            await mark_onboarding_handed_off(uow, pending_id)
    return context


async def mark_onboarding_handed_off(
    uow: SqlAlchemyUnitOfWork, pending_id: UUID
) -> None:
    """The held message has been handed to the run pipeline: forget its content."""
    row = await uow.session.get(PendingChatOnboarding, pending_id, with_for_update=True)
    if row is not None and row.handed_off_at is None:
        row.original_event = None
        row.handed_off_at = datetime.now(timezone.utc)


async def _shared_replay_context(
    uow: SqlAlchemyUnitOfWork,
    state: PendingState,
    event: ParsedInboundSurfaceEvent,
    user: UserEntity,
) -> AgentSurfaceContext:
    handler = build_surface_ingress(uow)
    # Resolved rather than read back. This used to load the surface saved in the
    # user's preferences and raise when it had gone -- and it can have gone by
    # the time a replay runs: the pod deleted, the person removed from it,
    # another device finishing onboarding first. Raising there dropped the very
    # message this whole flow exists to deliver. Selection honours that saved
    # default as its first choice anyway, and has continuity and a deterministic
    # tiebreak behind it, so asking it is strictly more answers and the same
    # preference.
    candidates = await SurfaceRepository(uow).list_active_for_routing(
        event.platform.value, system_credentials_only=True
    )
    surface = await handler.router.reachable_surface(
        candidates=candidates,
        user_id=user.id,
        platform=event.platform,
        parsed=event,
    )
    if surface is None:
        raise ValueError("The shared personal surface is missing")
    adapter = handler.adapter_registry.get(state.platform)
    if adapter is None:
        raise ValueError("The shared platform adapter is unavailable")
    context = await handler._prepare_surface_context(
        surface=surface,
        parsed=event,
        claim_delivery=False,
        adapter=adapter,
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=user.id,
            external_user_id=event.sender_external_user_id,
            email=str(user.email),
            phone=user.mobile_number,
            display_name=user.first_name,
        ),
    )
    if not isinstance(context, SurfaceChatContext):
        raise ValueError("The shared personal route is unavailable")
    return context


async def _expired_reply_context(
    uow: SqlAlchemyUnitOfWork,
    state: PendingState,
    destination: ParsedInboundSurfaceEvent,
) -> AgentSurfaceContext:
    # Completion remains useful after an administrator's delayed grant.
    # Expired content must not turn into an unexpected agent request.
    from app.modules.agent_surfaces.domain.ingress_context import (
        SurfaceReplyContext,
    )

    surface = (
        await SurfaceRepository(uow).get(state.installation_surface_id)
        if state.installation_surface_id
        else None
    )
    return SurfaceReplyContext(
        platform=destination.platform,
        surface_id=surface.id if surface else None,
        surface_account_id=surface.account_id if surface else None,
        surface_config=surface.config if surface else None,
        event=destination,
        reply_kind="identity_link",
        reply_message="Your workspace is ready. Send a new request to get started.",
    )
