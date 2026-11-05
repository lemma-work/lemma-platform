"""A per-agent token that changes whenever the agent's resource grants do.

What an agent's grants decide -- which function and agent tools it has, whether
it reaches pod data or connectors -- is derived from its grant rows, and a
caller that keeps that derivation for a while (the Agent Host tool bridge, which
would otherwise re-read it on every tool call) has to know when to throw it
away. Each write to an agent's grants replaces this token, and a cached
derivation records the token it was made under: a different token is a miss.

In Redis rather than in the process, because the write and the reader are
usually different processes. A token that cannot be read is reported as
unknown, and an unknown token matches nothing, so Redis being down degrades to
reading the grants every time rather than to serving a revoked one.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from uuid import UUID, uuid4

from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.db.session_uow import active_uow
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: The grantee type whose grants this tracks.
AGENT_GRANTEE = "AGENT"
#: The token an agent has before anything wrote its grants.
NEVER_CHANGED = "0"
#: Far longer than anything caches against it. A token that expires reads as
#: ``NEVER_CHANGED``, which no derivation made after a write recorded.
_TOKEN_TTL_SECONDS = 7 * 24 * 3600


@lru_cache(maxsize=1)
def _tokens() -> RedisJsonCache[str]:
    return RedisJsonCache(
        redis_url=settings.redis_url,
        key_prefix="authz:agent-grant-generation",
        ttl_seconds=_TOKEN_TTL_SECONDS,
    )


async def agent_grant_generation(agent_id: UUID) -> str | None:
    """The agent's current token, or ``None`` when it cannot be read."""
    try:
        token = await _tokens().get_json(str(agent_id))
    except RedisError, OSError, TimeoutError:
        logger.warning(
            "authorization.agent_grant_generation.read_failed.degraded",
            agent_id=str(agent_id),
            exc_info=True,
        )
        return None
    return token if isinstance(token, str) else NEVER_CHANGED


async def _replace(agent_ids: frozenset[UUID]) -> None:
    for agent_id in agent_ids:
        try:
            await _tokens().set_json(str(agent_id), uuid4().hex)
        except RedisError, OSError, TimeoutError:
            # A lost bump leaves a cached derivation in place until its own,
            # short, expiry.
            logger.warning(
                "authorization.agent_grant_generation.bump_failed.degraded",
                agent_id=str(agent_id),
                exc_info=True,
            )


async def bump_agent_grant_generations(
    session: AsyncSession, agent_ids: Iterable[UUID]
) -> None:
    """Replace each agent's token once the grant write in ``session`` commits.

    After the commit, so a reader cannot cache the old grants under the new
    token in between. Inline when there is no unit of work to defer to.
    """
    pending = frozenset(agent_ids)
    if not pending:
        return

    async def _run() -> None:
        await _replace(pending)

    uow = active_uow(session)
    if uow is None:
        await _run()
        return
    uow.after_commit(_run)
