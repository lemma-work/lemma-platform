"""Short-token store for Telegram inline-keyboard callbacks.

Telegram caps ``callback_data`` at 64 bytes, but an ask_user answer needs the
full callback id (``conversation_id|tool_call_id``), the question header and the
chosen value — well over 64 bytes. So each button carries only a short opaque
token; the real payload is stored in Redis under that token and resolved when
the user taps.

The TTL is a week, not an hour. It used to match a pause window that does not
exist: a run parked on a question or an approval waits as long as the person
takes, and a tap after the token expired resolved to nothing -- the button was
still on their screen and did nothing. Each entry is a few hundred bytes.

A token that is gone and a store that could not be read are different answers.
Folding the second into the first told somebody tapping during a Redis blip that
their button had expired and cleared it, when tapping again a moment later would
have worked. So a read failure raises `CallbackTokenUnreadable`, and only a
token that is genuinely absent or unparseable reads as ``None``.
"""

from __future__ import annotations

import secrets
from typing import Any

from redis.exceptions import RedisError

from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent_surfaces.caches import (
    TELEGRAM_CALLBACK_TOKENS,
    surface_caches,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: What an unreachable store raises. Anything else is a bug and propagates.
CALLBACK_STORE_ERRORS = (RedisError, OSError)


class CallbackTokenUnreadable(Exception):
    """The store could not be read, so whether the token exists is unknown."""


def _store() -> RedisJsonCache:
    return surface_caches.get(TELEGRAM_CALLBACK_TOKENS)


async def put_callback_token(payload: dict[str, Any]) -> str:
    """Store an interaction payload and return a short opaque token (16 chars).

    Raises one of `CALLBACK_STORE_ERRORS` when the store is unreachable: a
    caller rendering a keyboard falls back to words, and one adding an optional
    button sends without it.
    """
    token = secrets.token_hex(8)
    await _store().set_json(token, payload)
    return token


async def retry_keyboard() -> dict[str, list[list[dict[str, str]]]] | None:
    """A "Try again" button, or ``None`` when its token cannot be stored.

    The button is optional and the message it rides is not: it is the notice
    that a run failed. Minting the token used to be unguarded, so a Redis blip
    lost the notice along with the button, and the person heard nothing.
    """
    try:
        retry_token = await put_callback_token({"action": "retry"})
    except CALLBACK_STORE_ERRORS:
        logger.warning(
            "agent_surfaces.telegram.retry_button_unavailable.degraded",
            exc_info=True,
        )
        return None
    return {"inline_keyboard": [[{"text": "Try again", "callback_data": retry_token}]]}


async def get_callback_token(token: str) -> dict[str, Any] | None:
    """Resolve a callback token back to its stored payload, or ``None``.

    Raises `CallbackTokenUnreadable` when the store could not be asked.
    """
    if not token:
        return None
    try:
        stored = await _store().get_json(token)
    except CALLBACK_STORE_ERRORS as exc:
        raise CallbackTokenUnreadable() from exc
    except ValueError:
        # Not JSON: no tap will ever resolve it, which is what "expired" means.
        return None
    return stored if isinstance(stored, dict) else None
