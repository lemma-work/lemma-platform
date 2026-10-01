"""Signed app access tokens, and the cached answer to "may they still read it".

A private app is served from its own host, ``<slug>.<app_base_domain>``, which
the API's host-only session cookie never reaches. The app host's sign-in page
asks the API for a one-minute *ticket* -- the API reads its own session cookie,
which every app origin may already send it -- and trades the ticket on the app
host for a *cookie* there. Both are the same signed claims under different
signing purposes, so neither can stand in for the other, and neither needs a
server-side record: the signature is the record.

The cookie proves who the viewer was. Whether they may still read the app --
session alive, account in good standing, permission unchanged -- is asked again
at most every ``app_access_cache_ttl_seconds``, which is how late a sign-out or
a revoked share reaches the app's files. That is shared through Redis for the
same reason the account-standing cache is, and an outage there degrades to
asking again rather than to admitting anyone.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.core.config import settings
from app.core.crypto import get_secret_signer
from app.core.infrastructure.cache.resilient_cache import ResilientJsonCache
from app.modules.apps.config import apps_settings

TICKET_TTL_SECONDS = 60
COOKIE_TTL_SECONDS = 12 * 60 * 60


class AppAccessPurpose(StrEnum):
    """The signing purpose. Each derives its own key, so a ticket is not a cookie."""

    TICKET = "app-access-ticket"
    COOKIE = "app-access-cookie"


@dataclass(frozen=True, slots=True)
class AppAccessClaims:
    user_id: UUID
    app_id: UUID
    origin: str
    session_handle: str
    expires_at: int


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def mint_app_access_token(purpose: AppAccessPurpose, claims: AppAccessClaims) -> str:
    payload = json.dumps(
        {
            "u": str(claims.user_id),
            "a": str(claims.app_id),
            "o": claims.origin,
            "h": claims.session_handle,
            "e": claims.expires_at,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    # signer.sign returns "<kid>.<sig>"; the token is "<payload>.<kid>.<sig>".
    return f"{_b64e(payload)}.{get_secret_signer().sign(purpose.value, payload)}"


def verify_app_access_token(
    token: str | None,
    *,
    purpose: AppAccessPurpose,
    origin: str,
    now: int | None = None,
) -> AppAccessClaims | None:
    """The claims of an unexpired token signed for ``purpose`` and ``origin``."""
    if not token:
        return None
    try:
        payload_b64, signature = token.split(".", 1)
        payload = _b64d(payload_b64)
        if not get_secret_signer().verify(purpose.value, payload, signature):
            return None
        data: object = json.loads(payload)
        if not isinstance(data, dict):
            return None
        claims = AppAccessClaims(
            user_id=UUID(str(data["u"])),
            app_id=UUID(str(data["a"])),
            origin=str(data["o"]),
            session_handle=str(data["h"]),
            expires_at=int(data["e"]),
        )
    # A malformed token is the caller's problem, and the ways this body can be
    # malformed are few: the split, base64, JSON, `UUID(...)` and `int(...)`
    # raise ValueError, and a missing claim raises KeyError. Anything else is a
    # bug here, and must not be reported as somebody's bad cookie.
    except ValueError, KeyError:
        return None
    if claims.origin != origin:
        return None
    if claims.expires_at <= (int(time.time()) if now is None else now):
        return None
    return claims


_host_access_cache: ResilientJsonCache | None = None


def _get_cache() -> ResilientJsonCache | None:
    global _host_access_cache
    ttl = apps_settings.app_access_cache_ttl_seconds
    if ttl <= 0:
        return None
    if _host_access_cache is None or _host_access_cache.ttl_seconds != ttl:
        _host_access_cache = ResilientJsonCache(
            name="app_host_access_cache",
            key_prefix="apps:host-access",
            redis_url=settings.redis_url,
            ttl_seconds=ttl,
        )
    return _host_access_cache


def _cache_key(claims: AppAccessClaims) -> str:
    # The origin carries the release label, so a preview host -- which asks for
    # edit permission, not just read -- never borrows the live host's answer.
    return f"{claims.user_id}:{claims.session_handle}:{claims.app_id}:{claims.origin}"


async def host_access_is_cached(claims: AppAccessClaims) -> bool:
    cache = _get_cache()
    if cache is None:
        return False
    return await cache.get(_cache_key(claims), lambda payload: payload == "1") is True


async def remember_host_access(claims: AppAccessClaims) -> None:
    cache = _get_cache()
    if cache is not None:
        await cache.set(_cache_key(claims), "1")
