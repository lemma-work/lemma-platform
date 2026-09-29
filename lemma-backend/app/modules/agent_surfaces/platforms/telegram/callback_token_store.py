"""Short-token store for Telegram inline-keyboard callbacks.

Telegram caps ``callback_data`` at 64 bytes, but an ask_user answer needs the
full callback id (``conversation_id|tool_call_id``), the question header and the
chosen value — well over 64 bytes. So each button carries only a short opaque
token; the real payload is stored in Redis under that token and resolved when
the user taps. Redis being unavailable degrades to the formatted-text fallback
(the put/get just fail softly).

The TTL is a week, not an hour. It used to match a pause window that does not
exist: a run parked on a question or an approval waits as long as the person
takes, and a tap after the token expired resolved to nothing -- the button was
still on their screen and did nothing. Each entry is a few hundred bytes.
"""

from __future__ import annotations

import secrets
from typing import Any

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache

_CALLBACK_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60

_token_store: RedisJsonCache | None = None


def _store() -> RedisJsonCache:
    global _token_store
    if _token_store is None or _token_store._redis_url != settings.redis_url:
        _token_store = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="surface:telegram-cb",
            ttl_seconds=_CALLBACK_TOKEN_TTL_SECONDS,
        )
    return _token_store


async def put_callback_token(payload: dict[str, Any]) -> str:
    """Store an interaction payload and return a short opaque token (16 chars)."""
    token = secrets.token_hex(8)
    await _store().set_json(token, payload)
    return token


async def get_callback_token(token: str) -> dict[str, Any] | None:
    """Resolve a callback token back to its stored payload, or ``None``."""
    if not token:
        return None
    try:
        return await _store().get_json(token)
    except Exception:
        return None
