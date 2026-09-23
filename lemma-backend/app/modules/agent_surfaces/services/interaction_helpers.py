from __future__ import annotations

from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent_surfaces.services.display_resource_renderer import (
    parse_callback_id,
)

logger = get_logger(__name__)


def parse_interaction_target(parsed) -> tuple[UUID, str] | None:
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


def interaction_sender_matches(link, parsed) -> bool:
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
    link_id = _external_id(getattr(link, "external_user_id", None))
    sender_id = _external_id(getattr(parsed, "external_user_id", None))
    return bool(link_id) and bool(sender_id) and link_id == sender_id


def _within_authorized_scope(link, authorized_surface_ids) -> bool:
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
    return link.surface_id in set(authorized_surface_ids)


async def resolve_interaction_delivery(
    ingress,
    parsed,
    conversation_id: UUID,
    *,
    authorized_surface_ids=None,
):
    link = await ingress.conversation_link_repository.get_by_conversation_id(
        conversation_id
    )
    if link is None or link.platform != parsed.platform.value:
        logger.debug(
            "agent_surfaces.ingress_service."
            "surface_interaction_dropped_no_matching.diagnostic",
            conversation_id=conversation_id,
        )
        return None
    if not _within_authorized_scope(link, authorized_surface_ids):
        logger.warning(
            "agent_surfaces.ingress_service.surface_interaction_out_of_scope.degraded",
            conversation_id=conversation_id,
            surface_id=link.surface_id,
        )
        return None
    return await _resolve_link_delivery(ingress, parsed, link)


async def resolve_current_interaction_delivery(
    ingress, parsed, *, authorized_surface_ids=None
):
    external_thread_id = str(parsed.external_thread_id or "").strip()
    if not external_thread_id:
        return None
    # Narrowed at the read, not only checked after it: the continuity lookup is
    # the one link read not scoped to a surface, so an unnarrowed answer here
    # could name a thread in another tenant and be discarded a step later --
    # correct, but it makes the scope check look optional when it is the point.
    surface_id = (
        await ingress.conversation_link_repository.find_surface_id_for_external_thread(
            platform=parsed.platform.value,
            external_channel_id=parsed.external_channel_id,
            external_thread_id=external_thread_id,
            external_user_id=parsed.external_user_id,
            surface_ids=authorized_surface_ids,
        )
    )
    if surface_id is None:
        return None
    link = await ingress.conversation_link_repository.get_by_external_thread(
        surface_id=surface_id,
        platform=parsed.platform.value,
        external_channel_id=parsed.external_channel_id,
        external_thread_id=external_thread_id,
        external_user_id=parsed.external_user_id,
    )
    if link is None:
        return None
    if not _within_authorized_scope(link, authorized_surface_ids):
        logger.warning(
            "agent_surfaces.ingress_service.surface_interaction_out_of_scope.degraded",
            conversation_id=link.conversation_id,
            surface_id=link.surface_id,
        )
        return None
    return await _resolve_link_delivery(ingress, parsed, link)


async def _resolve_link_delivery(ingress, parsed, link):
    surface = await ingress.surface_repository.get(link.surface_id)
    if surface is None or not surface.is_active:
        logger.debug(
            "agent_surfaces.ingress_service."
            "surface_interaction_dropped_surface_missing.diagnostic",
            conversation_id=link.conversation_id,
            surface_id=link.surface_id,
        )
        return None
    adapter = ingress.adapter_registry.get(surface.surface_type)
    if adapter is None:
        return None
    credentials = await ingress.credential_resolver.for_surface(surface)
    return link, surface, adapter, credentials
