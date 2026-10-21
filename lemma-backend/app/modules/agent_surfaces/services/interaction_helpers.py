from __future__ import annotations

from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials

from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.domain.ports import SurfaceInstallationRepositoryPort
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.services.channel_places import ChannelSeats
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.display_resource_renderer import (
    parse_callback_id,
)
from app.modules.channels.contracts import Source, SourceKind

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class InteractionThread:
    """The conversation a tap answers, and the place its card was shown in."""

    conversation_id: UUID
    source: Source
    #: The platform id of the person the conversation answers: the one who
    #: may tap. A private place's person, or who said the line being answered
    #: in a group.
    person_external_id: str | None


#: Where a submission was matched to: the thread, the surface that owns it,
#: the adapter to answer through, and the credentials to answer with.
InteractionDelivery = tuple[
    InteractionThread,
    AgentSurfaceEntity,
    SurfacePlatformAdapterPort,
    SurfaceCredentials,
]


class InteractionIngress(Protocol):
    """The collaborators these lookups read from the ingress service.

    They were handed the whole service -- every mixin, untyped -- when what they
    touch is this. Naming it is what lets the type checker follow a call like
    ``ingress.credential_resolver.for_surface`` instead of reading it as
    possibly-anything.
    """

    seats: ChannelSeats
    surface_repository: SurfaceInstallationRepositoryPort
    adapter_registry: SurfacePlatformAdapterRegistry
    credential_resolver: SurfaceCredentialResolver


def parse_interaction_target(
    parsed: ParsedSurfaceInteraction,
) -> tuple[UUID, str] | None:
    raw_target = parse_callback_id(parsed.callback_id)
    if raw_target is None or not raw_target[0]:
        logger.debug(
            "agent_surfaces.ingress_service."
            "surface_interaction_dropped_unparseable_callback.diagnostic",
            callback_id=parsed.callback_id,
        )
        return None
    try:
        return UUID(raw_target[0]), raw_target[1]
    except ValueError:
        logger.debug(
            "agent_surfaces.ingress_service."
            "surface_interaction_dropped_invalid_conversation.diagnostic",
            callback_id=parsed.callback_id,
        )
        return None


def _external_id(value) -> str:
    """One external user id, folded so two spellings of a person are one person.

    Neither side was normalized before, so a platform that varies the case of an
    id between a message and an interaction read as a different human.
    """
    return str(value or "").strip().casefold()


def interaction_sender_matches(
    thread: InteractionThread, parsed: ParsedSurfaceInteraction
) -> bool:
    """May this person resolve the interaction shown in this conversation?

    Both ids must be present and equal. This used to return True whenever
    *either* was empty, which is fail-open on the only authorization control
    standing in front of a native Approve button: the tap resolves a
    `request_approval`, and the action it approves then runs. Both sides can be
    empty in ordinary traffic -- a thread opened by a notification whose
    channel had no address, a Slack payload with no `event.user`, a Teams one
    with neither `aadObjectId` nor `from.id` -- so "we could not tell who tapped"
    was a common state, and it meant "anyone may".

    Refusing when we cannot tell costs a person the button and not the answer:
    typing the decision resolves the same pause through
    `maybe_resume_pending_interaction`, which does not consult this.
    """
    person_id = _external_id(getattr(thread, "person_external_id", None))
    sender_id = _external_id(getattr(parsed, "external_user_id", None))
    return bool(person_id) and bool(sender_id) and person_id == sender_id


def _within_authorized_scope(
    source: Source,
    authorized_surface_ids: Collection[UUID] | None,
) -> bool:
    """Was this request allowed to act on the surface holding that conversation?

    `None` means "no receiver list", which happens only where the payload was
    verified with a secret of *ours* -- the shared Telegram bot, the shared
    WhatsApp number. Those serve every pod, so the delivering receiver names no
    subset and the sender match is the control.

    A list means the opposite, and is the case that matters: Slack and Teams
    tenants hold their own signing secrets, so anybody running their own app can
    mint a correctly-signed payload with whatever `callback_id` and `user.id`
    they like. The button's value is an unsigned `conversation_id|tool_call_id`,
    so without this the id alone decided whose conversation was resolved -- and
    an approval taken on it runs the paused tool call as that person, in their
    pod. The receiver list is derived from the workspace the signature actually
    proved (`slack_webhook_verification`), which is the boundary the payload
    could not forge.
    """
    if authorized_surface_ids is None:
        return True
    return source.surface_id in set(authorized_surface_ids)


async def resolve_interaction_delivery(
    ingress: InteractionIngress,
    parsed: ParsedSurfaceInteraction,
    conversation_id: UUID,
    *,
    authorized_surface_ids: Collection[UUID] | None = None,
) -> InteractionDelivery | None:
    """The thread of the conversation a card names: where its answer goes."""
    address = await ingress.seats.reply_address(conversation_id)
    source = address.source if address else None
    if source is None or source.platform != parsed.platform.value:
        logger.debug(
            "agent_surfaces.ingress_service."
            "surface_interaction_dropped_no_matching.diagnostic",
            conversation_id=conversation_id,
        )
        return None
    if not _within_authorized_scope(source, authorized_surface_ids):
        logger.warning(
            "agent_surfaces.ingress_service.surface_interaction_out_of_scope.degraded",
            conversation_id=conversation_id,
            surface_id=source.surface_id,
        )
        return None
    heard = (address.ref or {}) if address else {}
    # As identity resolution names a person: the directory id where the
    # platform gives one (Teams' AAD object id), else its own.
    heard_from = heard.get("sender_aad_object_id") or heard.get(
        "sender_external_user_id"
    )
    person = (
        source.external_user_id
        if source.kind is SourceKind.PERSON
        else (heard_from if isinstance(heard_from, str) else None)
    )
    return await _resolve_thread_delivery(
        ingress,
        InteractionThread(
            conversation_id=conversation_id, source=source, person_external_id=person
        ),
    )


async def resolve_current_interaction_delivery(
    ingress: InteractionIngress,
    parsed: ParsedSurfaceInteraction,
    *,
    authorized_surface_ids: Collection[UUID] | None = None,
) -> InteractionDelivery | None:
    """The tapper's own conversation where the tap was made: a button that
    names no conversation, like Retry.

    The place is the chat when the bot hears it as a group, else the
    tapper's private chat; the conversation is the one that last took up
    something they said there. Both reads are narrowed to the surfaces the
    request proved, so an answer can never name another tenant's thread.
    """
    person = str(parsed.external_user_id or "").strip()
    if not person:
        return None
    platform = parsed.platform.value
    surface_ids = (
        None if authorized_surface_ids is None else list(authorized_surface_ids)
    )
    thread: str | None = None
    places: list[Source] = []
    if parsed.external_channel_id:
        places = await ingress.seats.places_hearing(
            platform=platform,
            external_channel_id=parsed.external_channel_id,
            surface_ids=surface_ids,
        )
        thread = str(parsed.external_thread_id or "").strip() or None
    if not places:
        thread = None
        places = await ingress.seats.person_places(
            platform=platform,
            external_user_id=person,
            surface_ids=surface_ids,
            limit=1,
        )
    for source in places:
        conversation_id = await ingress.seats.conversation_at(
            source.id, sender_external_id=person, external_thread_id=thread
        )
        if conversation_id is not None:
            return await _resolve_thread_delivery(
                ingress,
                InteractionThread(
                    conversation_id=conversation_id,
                    source=source,
                    person_external_id=person,
                ),
            )
    return None


async def _resolve_thread_delivery(
    ingress: InteractionIngress, thread: InteractionThread
) -> InteractionDelivery | None:
    surface_id = thread.source.surface_id
    surface = await ingress.surface_repository.get(surface_id) if surface_id else None
    if surface is None or not surface.is_active:
        logger.debug(
            "agent_surfaces.ingress_service."
            "surface_interaction_dropped_surface_missing.diagnostic",
            conversation_id=thread.conversation_id,
            surface_id=surface_id,
        )
        return None
    adapter = ingress.adapter_registry.get(surface.surface_type)
    if adapter is None:
        return None
    credentials = await ingress.credential_resolver.for_surface(surface)
    return thread, surface, adapter, credentials
