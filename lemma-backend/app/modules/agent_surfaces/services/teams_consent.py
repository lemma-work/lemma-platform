"""The Teams admin-consent handshake: consent URLs and their nonces.

The callback is excluded from the auth middleware, so `state` is the only thing
tying a callback to a consent flow this server actually started. A bare surface
id cannot do that job: it is shown to every pod member who opens Teams setup and
never rotates. These nonces are what make `state` unguessable and spendable once.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlencode
from uuid import UUID

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent_surfaces.config import surface_settings

# Well past a real consent round-trip, short enough that an observed URL stops
# being useful quickly.
_TTL_SECONDS = 3600
_cache: RedisJsonCache | None = None


def _get_cache() -> RedisJsonCache:
    global _cache
    if _cache is None or _cache._redis_url != settings.redis_url:
        _cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="surface:teams-consent-nonce",
            ttl_seconds=_TTL_SECONDS,
        )
    return _cache


async def build_consent_url(surface_id: UUID, tenant_id: str) -> str:
    """The Microsoft consent URL, with `state` bound to a single-use nonce."""
    nonce = secrets.token_urlsafe(32)
    await _get_cache().set_raw(str(surface_id), nonce)
    callback_base = settings.api_url.rstrip("/")
    params = urlencode({
        "client_id": surface_settings.microsoft_bot_app_id or "",
        "redirect_uri": f"{callback_base}/surfaces/teams/admin-consent/callback",
        "state": f"{surface_id}:{nonce}",
    })
    return f"https://login.microsoftonline.com/{tenant_id}/adminconsent?{params}"


async def consume_nonce(surface_id: UUID, nonce: str) -> bool:
    """Spend the nonce issued for this surface, returning whether it was valid.

    Compare-and-delete in one step, so a replayed callback loses the race
    rather than being honoured twice.
    """
    if not nonce:
        return False
    return await _get_cache().delete_if_value(str(surface_id), nonce)
