"""What is wrong with a surface right now, said where its members look.

A surface whose webhook the platform never accepted is ``ERROR`` and receives
nothing. A bot whose credentials the platform refuses still receives -- the
platform signs what it sends with another secret -- but nothing it sends
arrives; it stays ``ACTIVE``, since switching it off would drop what people
write to it too. Either way the surface's readers are told why: the reason is
kept here, shown on the surface (``problem``) and in its setup, and cleared
once a save registers it again or a send goes through.

Kept in Redis, by surface, for ``PROBLEM_KEPT_FOR``: a fact about the
platform's last answer rather than about the surface's configuration, and one
that renews itself with every refusal. Redis away loses the reason, never the
status, and never fails the caller.
"""

from __future__ import annotations

from uuid import UUID

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    AgentSurfaceStatus,
)
from app.modules.agent_surfaces.services.channel_lines import WEB
from app.modules.agent_surfaces.services.delivery_outcome import (
    Outcome,
    refused_credentials,
)
from app.modules.channels.contracts import (
    Channel,
    ChannelKind,
    Source,
    call_for_person,
)

logger = get_logger(__name__)

#: How long a problem is shown without the platform saying it again.
PROBLEM_KEPT_FOR = 30 * 86_400

_problems: RedisJsonCache[dict[str, str]] | None = None


def _cache() -> RedisJsonCache[dict[str, str]]:
    global _problems
    if _problems is None:
        _problems = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="surface:problem",
            ttl_seconds=PROBLEM_KEPT_FOR,
        )
    return _problems


async def note_problem(surface_id: UUID, reason: str) -> None:
    """The surface cannot do its work, and why -- in words for its members."""
    try:
        await _cache().set_json(str(surface_id), {"reason": reason[:500]})
    except (RedisError, OSError) as exc:
        logger.warning(
            "agent_surfaces.surface_problems.unavailable.degraded",
            error_type=type(exc).__name__,
        )
    logger.warning(
        "agent_surfaces.surface_problems.noted.degraded", surface_id=str(surface_id)
    )


async def clear_problem(surface_id: UUID) -> None:
    try:
        await _cache().delete(str(surface_id))
    except (RedisError, OSError) as exc:
        logger.warning(
            "agent_surfaces.surface_problems.unavailable.degraded",
            error_type=type(exc).__name__,
        )


async def problem_of(surface_id: UUID) -> str | None:
    """Why the surface cannot do its work, if it cannot."""
    try:
        found = await _cache().get_json(str(surface_id))
    except (RedisError, OSError) as exc:
        logger.warning(
            "agent_surfaces.surface_problems.unavailable.degraded",
            error_type=type(exc).__name__,
        )
        return None
    reason = found.get("reason") if isinstance(found, dict) else None
    return reason if isinstance(reason, str) and reason else None


#: An ``ERROR`` surface whose reason was not kept (Redis lost it).
NEVER_REGISTERED = (
    "The platform never accepted this surface's webhook, so nothing arrives. "
    "Save it again to retry."
)


async def surface_problem(surface: AgentSurfaceEntity) -> str | None:
    """Why the surface cannot do its work, if it cannot."""
    noted = await problem_of(surface.id)
    if noted is None and surface.status is AgentSurfaceStatus.ERROR:
        return NEVER_REGISTERED
    return noted


async def heed_send(
    uow_factory: UnitOfWorkFactory, channel: Channel, source: Source, outcome: Outcome
) -> None:
    """What a send to a place says of its bot. Refused credentials: the
    platform refuses every send of the bot, so its surface says so, and a
    contact it was writing to waits on a person. A send that went: the bot
    works again."""
    if source.surface_id is None or source.platform == WEB:
        return
    if outcome.sent:
        await clear_problem(source.surface_id)
        return
    if not refused_credentials(outcome):
        return
    where = source.platform.title()
    await note_problem(
        source.surface_id,
        f"{where} refused the bot's credentials (HTTP {outcome.provider_status}): "
        "nothing it sends arrives. Reconnect its account.",
    )
    if channel.kind is ChannelKind.CONTACT:
        async with uow_factory() as uow:
            await call_for_person(
                uow,
                channel_id=channel.id,
                reason=f"Nothing reaches them: {where} refused the bot",
            )
            await uow.commit()


__all__ = [
    "PROBLEM_KEPT_FOR",
    "clear_problem",
    "heed_send",
    "note_problem",
    "problem_of",
    "surface_problem",
]
