"""Asking the platform whether a place is private before its mirror is made.

A Slack or Teams place's channel is made the first time the bot hears it, and
is private or public from then on. What the event says is not always enough:
an ``app_mention`` -- often a channel's first event -- names no channel type,
and a Teams message never names a channel's membership. So before the place
is made, and only then, the platform is asked (``PlacePrivacyReader``), and
its answer rides on the event's metadata for whatever makes the place next
(``workspace_channels``, ``conversation_binder``). An answer that cannot be
had leaves the event as it was, and a place nothing said is public is made
private (``domain/place_privacy``).

The answer is kept a few minutes in Redis, so a burst of first messages asks
once; a failed lookup is not kept.
"""

from __future__ import annotations

from collections.abc import Sequence

from redis.exceptions import RedisError

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.caches import PLACE_PRIVACY, surface_caches
from app.modules.agent_surfaces.domain.adapter_port import place_privacy_reader
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.place_privacy import (
    PlaceFacts,
    PlacePrivacy,
    said_privacy,
)
from app.modules.agent_surfaces.services.channel_places import ChannelSeats
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)

logger = get_logger(__name__)

#: The platforms whose places are mirrored public or private by what they say.
_ASKED = frozenset({SurfacePlatform.SLACK, SurfacePlatform.TEAMS})


async def settled_privacy(
    uow: SqlAlchemyUnitOfWork,
    *,
    adapter: object,
    credential_resolver: SurfaceCredentialResolver,
    seats: ChannelSeats,
    surfaces: Sequence[AgentSurfaceEntity],
    parsed: ParsedInboundSurfaceEvent,
) -> ParsedInboundSurfaceEvent:
    """The event, with the platform's word on its place's privacy where the
    event lacked it and a mirror of the place may be made now."""
    reader = place_privacy_reader(adapter)
    if reader is None or not _unsaid(parsed):
        return parsed
    mine = await _without_a_mirror(seats, surfaces, parsed)
    if not mine:
        return parsed  # every mirror is made already: changes come as events
    key = f"{parsed.platform.value}:{parsed.tenant_id or '-'}:{parsed.external_channel_id}"
    credentials = await credential_resolver.for_surface(mine[0])
    # Redis and the platform: no connection held for either.
    async with connection_released(uow.session):
        facts = await _recalled_place_facts(key)
        if facts is None:
            facts = await reader.read_place_privacy(
                credentials=credentials, event=parsed
            )
            if isinstance(facts, PlaceFacts):
                await _keep_place_facts(key, facts)
    if not isinstance(facts, PlaceFacts):
        return parsed
    return parsed.model_copy(update={"metadata": facts.onto(parsed.metadata)})


def _unsaid(parsed: ParsedInboundSurfaceEvent) -> bool:
    """A Slack or Teams group place whose privacy the event does not name."""
    return (
        not parsed.is_dm
        and parsed.platform in _ASKED
        and bool(parsed.external_channel_id)
        and said_privacy(parsed.platform, parsed.metadata) is None
    )


async def _without_a_mirror(
    seats: ChannelSeats,
    surfaces: Sequence[AgentSurfaceEntity],
    parsed: ParsedInboundSurfaceEvent,
) -> list[AgentSurfaceEntity]:
    """The surfaces of the event's platform with no mirror of its place yet."""
    mine = [surface for surface in surfaces if surface.surface_type is parsed.platform]
    if not mine or not parsed.external_channel_id:
        return []
    known = await seats.places_hearing(
        platform=parsed.platform.value,
        external_channel_id=parsed.external_channel_id,
        surface_ids=[surface.id for surface in mine],
    )
    mirrored = {place.surface_id for place in known}
    return [surface for surface in mine if surface.id not in mirrored]


async def _recalled_place_facts(key: str) -> PlaceFacts | None:
    try:
        stored = await surface_caches.get(PLACE_PRIVACY).get_json(key)
    except RedisError, OSError, ValueError:
        return None
    if not isinstance(stored, dict):
        return None
    try:
        return PlaceFacts(
            privacy=PlacePrivacy(stored["privacy"]),
            channel_type=stored.get("channel_type"),
            shared_externally=bool(stored.get("shared_externally")),
        )
    except ValueError, KeyError, TypeError:
        return None


async def _keep_place_facts(key: str, facts: PlaceFacts) -> None:
    try:
        await surface_caches.get(PLACE_PRIVACY).set_json(
            key,
            {
                "privacy": facts.privacy.value,
                "channel_type": facts.channel_type,
                "shared_externally": facts.shared_externally,
            },
        )
    except RedisError, OSError:
        logger.info("agent_surfaces.place_privacy.cache_unavailable.observed")


__all__ = ["settled_privacy"]
