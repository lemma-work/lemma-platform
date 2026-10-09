"""Visitors' sessions, for the surfaces that open them on the web.

A session is the browser's hold on who a visitor is (see
``domain/visitor_sessions``); the surface decides when one is opened, refreshed
or upgraded, and this module keeps it. Its secret is returned once, when minted
or rotated, and stored only as a digest.

Whether a session is still live is asked on every request a visitor makes, so
the answer is cached in Redis for a few seconds. Revoking a session deletes the
cached answer once the revocation is committed, so it reaches every replica on
the next request rather than when the cache expires.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterable
from datetime import datetime, timezone
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.contacts.domain.visitor_sessions import (
    VisitorSession,
    VisitorStrength,
    expiry_on_use,
    first_expiry,
)
from app.modules.contacts.infrastructure.visitor_sessions import (
    VisitorSessionRepository,
)

__all__ = [
    "VisitorSession",
    "VisitorStrength",
    "add_visitor_code",
    "attach_visitor_conversation",
    "consume_visitor_code",
    "conversation_has_visitor",
    "forget_session_liveness",
    "identify_visitor_session",
    "latest_visitor_conversation",
    "open_visitor_session",
    "renew_visitor_session",
    "revoke_visitor_sessions",
    "spend_code_attempt",
    "sweep_visitor_codes",
    "sweep_visitor_sessions",
    "touch_visitor_session",
    "visitor_session",
    "visitor_session_by_secret",
    "visitor_session_is_live",
]

logger = get_logger(__name__)

SECRET_PREFIX = "vs_"
#: How long a live answer is trusted: the most a revocation that missed its
#: cache delete (Redis away at that moment) can lag.
_LIVE_SECONDS = 30
#: An ended session stays ended, so its answer can be kept as long as any
#: access token naming it could still be presented.
_ENDED_SECONDS = 900

_liveness: RedisJsonCache[dict[str, bool]] | None = None


def _liveness_cache() -> RedisJsonCache[dict[str, bool]]:
    global _liveness
    if _liveness is None:
        _liveness = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="visitor:live",
            ttl_seconds=_LIVE_SECONDS,
        )
    return _liveness


def _digest(secret: str) -> str:
    # The secret is 256 random bits, so an unkeyed hash is as good as any.
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _mint_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# -- sessions ---------------------------------------------------------------


async def open_visitor_session(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    widget_id: UUID,
    contact_id: UUID | None,
    strength: VisitorStrength,
    ip_hash: str | None,
) -> tuple[VisitorSession, str]:
    """A new session, and the secret that is the only way back to it."""
    secret = _mint_secret()
    session = await VisitorSessionRepository(uow.session).open(
        pod_id=pod_id,
        widget_id=widget_id,
        contact_id=contact_id,
        strength=strength,
        secret_hash=_digest(secret),
        expires_at=first_expiry(strength, _now()),
        ip_hash=ip_hash,
    )
    return session, secret


async def visitor_session(
    uow: SqlAlchemyUnitOfWork, session_id: UUID
) -> VisitorSession | None:
    return await VisitorSessionRepository(uow.session).get(session_id)


async def visitor_session_by_secret(
    uow: SqlAlchemyUnitOfWork, *, widget_id: UUID, secret: str
) -> VisitorSession | None:
    """The session this secret opens on this widget, live or not."""
    return await VisitorSessionRepository(uow.session).by_secret(
        widget_id=widget_id, secret_hash=_digest(secret)
    )


async def renew_visitor_session(
    uow: SqlAlchemyUnitOfWork, session: VisitorSession
) -> None:
    """Note that the session was used; a contact's lasts longer for it."""
    await VisitorSessionRepository(uow.session).renew(
        session.id, expires_at=expiry_on_use(session, _now())
    )


async def identify_visitor_session(
    uow: SqlAlchemyUnitOfWork,
    session: VisitorSession,
    *,
    contact_id: UUID,
    strength: VisitorStrength,
) -> str:
    """Make the session this contact's, under a new secret.

    The old secret named an anonymous visitor; anybody who copied it then must
    not hold a contact's session now.
    """
    secret = _mint_secret()
    await VisitorSessionRepository(uow.session).identify(
        session.id,
        contact_id=contact_id,
        strength=strength,
        secret_hash=_digest(secret),
        expires_at=first_expiry(strength, _now()),
    )
    return secret


async def attach_visitor_conversation(
    uow: SqlAlchemyUnitOfWork, session_id: UUID, *, conversation_id: UUID
) -> None:
    await VisitorSessionRepository(uow.session).attach_conversation(
        session_id, conversation_id=conversation_id
    )


async def touch_visitor_session(uow: SqlAlchemyUnitOfWork, session_id: UUID) -> None:
    await VisitorSessionRepository(uow.session).touch(session_id)


async def revoke_visitor_sessions(
    uow: SqlAlchemyUnitOfWork,
    *,
    widget_id: UUID | None = None,
    contact_id: UUID | None = None,
    strength: VisitorStrength | None = None,
) -> list[UUID]:
    """End the matching sessions. Pass the ids to ``forget_session_liveness``
    once the transaction has committed."""
    return await VisitorSessionRepository(uow.session).revoke(
        widget_id=widget_id, contact_id=contact_id, strength=strength
    )


async def forget_session_liveness(session_ids: Iterable[UUID]) -> None:
    """Drop the cached answers for sessions that just ended."""
    ids = [str(session_id) for session_id in session_ids]
    if not ids:
        return
    cache = _liveness_cache()
    try:
        for session_id in ids:
            await cache.delete(session_id)
    except (RedisError, OSError) as exc:
        # Each cached "live" lapses within _LIVE_SECONDS on its own.
        logger.warning(
            "contacts.visitor_sessions.liveness_unavailable.degraded",
            error_type=type(exc).__name__,
        )


async def visitor_session_is_live(
    session_id: UUID, *, uow_factory: UnitOfWorkFactory
) -> bool:
    """Whether the session is neither revoked nor past its end.

    Fails to the database, not to "live": a visitor's request costs one more
    read while Redis is away, never a revoked session honoured.
    """
    cache = _liveness_cache()
    key = str(session_id)
    try:
        cached = await cache.get_json(key)
    except (RedisError, OSError) as exc:
        logger.warning(
            "contacts.visitor_sessions.liveness_unavailable.degraded",
            error_type=type(exc).__name__,
        )
        cached = None
    if isinstance(cached, dict) and isinstance(cached.get("live"), bool):
        return cached["live"]
    async with uow_factory() as uow:
        session = await VisitorSessionRepository(uow.session).get(session_id)
    now = _now()
    live = session is not None and session.is_live(now)
    ttl = _ENDED_SECONDS
    if live and session is not None:
        ttl = max(
            1, min(_LIVE_SECONDS, int((session.expires_at - now).total_seconds()))
        )
    try:
        await cache.set_json(key, {"live": live}, ttl_seconds=ttl)
    except (RedisError, OSError) as exc:
        logger.warning(
            "contacts.visitor_sessions.liveness_unavailable.degraded",
            error_type=type(exc).__name__,
        )
    return live


# -- codes ------------------------------------------------------------------


async def add_visitor_code(
    uow: SqlAlchemyUnitOfWork,
    *,
    session_id: UUID,
    email: str,
    code_hash: str,
    expires_at: datetime,
) -> None:
    await VisitorSessionRepository(uow.session).add_code(
        session_id=session_id, email=email, code_hash=code_hash, expires_at=expires_at
    )


async def spend_code_attempt(
    uow: SqlAlchemyUnitOfWork, *, session_id: UUID, email: str, max_attempts: int
) -> tuple[UUID, str] | None:
    """Count one guess and return the live code's id and digest to compare.

    Counted before the comparison, in one statement, so a guess is spent
    whether it is right or wrong and however many arrive at once.
    """
    return await VisitorSessionRepository(uow.session).spend_attempt(
        session_id=session_id, email=email, max_attempts=max_attempts
    )


async def consume_visitor_code(uow: SqlAlchemyUnitOfWork, code_id: UUID) -> bool:
    return await VisitorSessionRepository(uow.session).consume_code(code_id)


# -- reads for routing --------------------------------------------------------


async def conversation_has_visitor(
    session: AsyncSession, conversation_id: UUID
) -> bool:
    """Whether a visitor's session leads to this conversation."""
    return await VisitorSessionRepository(session).leads_to(conversation_id)


async def latest_visitor_conversation(
    session: AsyncSession, contact_id: UUID
) -> tuple[UUID, datetime] | None:
    """The contact's most recently used web conversation, and when."""
    return await VisitorSessionRepository(session).latest_for_contact(contact_id)


# -- retention ----------------------------------------------------------------


async def sweep_visitor_sessions(
    uow: SqlAlchemyUnitOfWork, *, now: datetime, batch: int
) -> int:
    return await VisitorSessionRepository(uow.session).sweep(now=now, batch=batch)


async def sweep_visitor_codes(
    uow: SqlAlchemyUnitOfWork, *, now: datetime, batch: int
) -> int:
    return await VisitorSessionRepository(uow.session).sweep_codes(now=now, batch=batch)
