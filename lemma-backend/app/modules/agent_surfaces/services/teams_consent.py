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
from app.modules.agent_surfaces.caches import TEAMS_CONSENT_NONCES, surface_caches
from app.modules.agent_surfaces.config import surface_settings

# Well past a real consent round-trip, short enough that an observed URL stops
# being useful quickly.
_TTL_SECONDS = TEAMS_CONSENT_NONCES.ttl_seconds


def _get_cache() -> RedisJsonCache:
    return surface_caches.get(TEAMS_CONSENT_NONCES)


async def _live_nonce(surface_id: UUID) -> str:
    """The surface's outstanding nonce, minted only when none is outstanding.

    Minted once and reused until it is spent or expires: reading setup state
    used to mint a fresh one each time, which voided the link an editor had
    already sent to their tenant administrator whenever anyone opened the
    setup panel again.
    """
    cache = _get_cache()
    key = str(surface_id)
    # Twice, because the outstanding nonce can expire between the refused
    # SET NX and the read that follows it.
    for _ in range(2):
        candidate = secrets.token_urlsafe(32)
        if await cache.set_raw_if_absent(key, candidate, ttl_seconds=_TTL_SECONDS):
            return candidate
        outstanding = await cache.get_raw(key)
        if outstanding:
            return outstanding
    candidate = secrets.token_urlsafe(32)
    await cache.set_raw(key, candidate, ttl_seconds=_TTL_SECONDS)
    return candidate


async def build_consent_url(surface_id: UUID, tenant_id: str) -> str:
    """The Microsoft consent URL, with `state` bound to a single-use nonce."""
    nonce = await _live_nonce(surface_id)
    callback_base = settings.api_url.rstrip("/")
    params = urlencode(
        {
            "client_id": surface_settings.microsoft_bot_app_id or "",
            "redirect_uri": f"{callback_base}/surfaces/teams/admin-consent/callback",
            "state": f"{surface_id}:{nonce}",
        }
    )
    return f"https://login.microsoftonline.com/{tenant_id}/adminconsent?{params}"


async def consume_nonce(surface_id: UUID, nonce: str) -> bool:
    """Spend the nonce issued for this surface, returning whether it was valid.

    Compare-and-delete in one step, so a replayed callback loses the race
    rather than being honoured twice.
    """
    if not nonce:
        return False
    return await _get_cache().delete_if_value(str(surface_id), nonce)
