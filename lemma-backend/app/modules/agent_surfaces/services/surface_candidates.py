"""Which of the deployment's surfaces an inbound event could be for.

Two narrowings, and they run in that order because each is cheaper than the one
after it. `fan_in_candidates` reduces a shared bot's whole fan-in to the pods
the sender actually belongs to; `admitted_surfaces` then keeps the ones whose
own rules say they should act on this event.

Functions taking their collaborators rather than an object of their own: each
needs one or two of them and calls back into neither, which is the same shape as
`surface_bulk_teardown` and `onboarding_replies`, and for the same reason. They
were methods on `SurfaceInboundMixin`, where they read `self.router`,
`self.surface_repository` and `self.conversation_link_repository` off a
namespace that never declared them -- eleven of that file's baselined type
errors were exactly that, and they are gone now that the names arrive as
arguments.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent_surfaces.domain.adapter_port import (
    SurfacePlatformAdapterPort,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceInstallationRepositoryPort,
)
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (  # noqa: E501
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    native_credentials,
)
from app.modules.agent_surfaces.services.surface_router import SurfaceRouter


async def fan_in_candidates(
    *,
    platform: str,
    parsed: ParsedInboundSurfaceEvent,
    adapter: SurfacePlatformAdapterPort,
    router: SurfaceRouter,
    surfaces: SurfaceInstallationRepositoryPort,
) -> tuple[list[AgentSurfaceEntity], ResolvedSurfaceUser, set[UUID] | None]:
    """The shared bot's fan-in, narrowed to the pods the sender is in.

    The fan-in is every system-credential surface of the platform in the
    deployment -- one per provisioned person -- read and hydrated on the way
    to picking the handful this sender can use. Narrowing it looks circular,
    because selection needs the sender and the sender was resolved from
    `candidates[0]`'s credentials. It is not, and the reason changed when
    WhatsApp numbers became a pool.

    It *used* to be that every candidate is a system-credential surface and
    `native_credentials` answers those from settings -- the same values
    whichever row asks. That is no longer true: a pooled number carries its
    own access token, so which row asks now decides what comes back. The
    ordering survives because sender resolution needs no credentials at all.
    `WhatsAppPlatformService.fetch_sender_profile` reads `sender_phone` and
    `sender_display_name` straight off the parsed webhook and makes no API
    call, so there is nothing for a token to authorise. The installation id
    is not needed either -- `resolve` consults it only for Slack and Teams,
    and neither has a shared bot.

    The arriving number then narrows beside the sender's pods rather than
    instead of them. It has to be beside: a pooled number may be held by
    several organisations, so on its own it names a number and not a
    customer.

    An unknown sender, or one who belongs to none of these pods, gets no
    candidates from here and the caller reads the fan-in unnarrowed. That is
    what keeps the answer identical rather than merely cheaper: selection
    falls back to the thread's existing surface for a non-member, and that
    surface is how ordinary ingestion tells them they have no access to the
    pod their conversation is in.
    """
    resolved_user = await router.resolve_sender(
        adapter=adapter,
        parsed=parsed,
        credentials=native_credentials(platform),
        installation_id=None,
    )
    if resolved_user.internal_user_id is None:
        return [], resolved_user, None
    # Through the router. Membership is the router's question -- it is what
    # `select_surface` and `matches_user` ask -- and holding a second copy
    # here meant the same question could be answered two ways in one
    # message, which is the class of bug this whole pass is removing.
    pod_ids = set(
        await router.pod_membership_port.get_user_pod_ids(
            resolved_user.internal_user_id
        )
    )
    if not pod_ids:
        return [], resolved_user, None
    return (
        await surfaces.list_active_for_routing(
            platform,
            pod_ids=pod_ids,
            system_credentials_only=True,
            surface_identity_id=parsed.reply_target.get("phone_number_id"),
        ),
        resolved_user,
        pod_ids,
    )


async def admitted_surfaces(
    surfaces: list[AgentSurfaceEntity],
    parsed: ParsedInboundSurfaceEvent,
    *,
    links: SurfaceConversationLinkRepository,
) -> list[AgentSurfaceEntity]:
    """The surfaces that should act on this event.

    Everything `allows_inbound_event` can decide on its own is decided
    first, and for almost every message that is the whole answer: a DM, an
    @mention, a channel the surface is not connected to. Only one case is
    left over, and only there is anything read.

    That case is a reply inside a channel thread with no mention in it.
    `is_thread_reply` comes from the payload -- Teams' `replyToId`, Slack's
    `thread_ts` -- so it says "this is a reply", never "this is a reply to
    *us*". Admitting on it alone meant every threaded reply by a pod member,
    anywhere in a connected channel, started an agent run: two colleagues
    talking under somebody else's message paid for a model call each time,
    and the run began with no conversation to continue.

    The link answers it, on the same key routing continues a conversation
    with -- thread *and* sender. That equivalence is the point rather than a
    convenience: if there is no link, `_route_to_surface` would not have
    continued anything either, it would have opened a fresh conversation
    with no history, which is precisely the run that should not happen. If
    there is one, the reply belongs to a conversation already under way and
    goes through exactly as before.

    The read is on the narrow path only, and it replaces an agent run rather
    than adding to one -- so the case that costs a query is the case that
    used to cost a model call.
    """
    admitted = [
        surface
        for surface in surfaces
        if surface.allows_inbound_event(parsed, thread_is_ours=False)
    ]
    if admitted or not parsed.metadata.get("is_thread_reply"):
        return admitted
    thread_surface_id = await links.find_surface_id_for_external_thread(
        platform=parsed.platform.value,
        external_channel_id=parsed.external_channel_id,
        external_thread_id=parsed.external_thread_id,
        external_user_id=parsed.sender_external_user_id,
        surface_ids=[surface.id for surface in surfaces],
    )
    if thread_surface_id is None:
        return []
    return [
        surface
        for surface in surfaces
        if surface.id == thread_surface_id
        and surface.allows_inbound_event(parsed, thread_is_ours=True)
    ]
