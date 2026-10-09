"""A web widget found by its public key, as every public request asks.

Every request a page makes names a widget by its key -- the CORS pre-flight
before the request, then the request itself -- so the answer is cached in
Redis briefly. A member's change (origins, who it answers, a new secret,
deletion) drops the entry, so it reaches every replica on the next request.

A key that names no widget is cached too, more briefly: a key that was never
issued is the cheapest thing for a stranger to send, and must not cost a
database read every time.
"""

from __future__ import annotations

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.web_widgets import WebWidget
from app.modules.agent_surfaces.infrastructure.repositories.web_widget_repository import (  # noqa: E501
    WebWidgetRepository,
)

logger = get_logger(__name__)

_FOUND_SECONDS = 60
_MISSING_SECONDS = 15

_cache: RedisJsonCache[dict[str, object]] | None = None


def _widgets() -> RedisJsonCache[dict[str, object]]:
    global _cache
    if _cache is None:
        _cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="web-widget:key",
            ttl_seconds=_FOUND_SECONDS,
        )
    return _cache


def _degraded(exc: Exception) -> None:
    logger.warning(
        "agent_surfaces.widget_directory.unavailable.degraded",
        error_type=type(exc).__name__,
    )


async def widget_by_key(
    public_key: str, *, uow_factory: UnitOfWorkFactory
) -> WebWidget | None:
    """The widget this key names, whether or not it is switched on."""
    try:
        cached = await _widgets().get_json(public_key)
    except (RedisError, OSError) as exc:
        _degraded(exc)
        cached = None
    if isinstance(cached, dict) and "widget" in cached:
        found = cached["widget"]
        return WebWidget.model_validate(found) if found else None
    async with uow_factory() as uow:
        widget = await WebWidgetRepository(uow.session).by_public_key(public_key)
    try:
        await _widgets().set_json(
            public_key,
            {"widget": widget.model_dump(mode="json") if widget else None},
            ttl_seconds=_FOUND_SECONDS if widget else _MISSING_SECONDS,
        )
    except (RedisError, OSError) as exc:
        _degraded(exc)
    return widget


async def forget_widget(public_key: str) -> None:
    """Drop the cached answer after a member changed or deleted the widget."""
    try:
        await _widgets().delete(public_key)
    except (RedisError, OSError) as exc:
        # The entry lapses on its own within _FOUND_SECONDS.
        _degraded(exc)
