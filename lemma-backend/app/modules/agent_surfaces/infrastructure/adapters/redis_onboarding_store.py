"""A half-finished signup, held between two messages from a stranger.

The inbound path had nowhere to put this. A `SurfaceReplyContext` is one reply
computed and forgotten, which is all a "please sign up" line ever needed -- but
asking for an address, mailing a code and waiting for it is a conversation with
somebody who has no user id, so there is no session and no conversation row to
hang it on.

Redis, keyed on the sender the platform signed, with a short life. Thirty
minutes is long enough to go and find a code in an inbox and short enough that
an abandoned attempt is gone before anyone returns to it, and it keeps every
exchange inside WhatsApp's free-form reply window without having to reason about
the window at all.

The code is stored hashed. It is short-lived and low-value, but it is also the
one thing standing between a sender and an account, and a Redis snapshot should
not contain a working one.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from redis.asyncio import Redis

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis
from app.modules.agent_surfaces.config import surface_settings

OnboardingStep = Literal["awaiting_email", "awaiting_code"]

#: Wrong codes allowed before the attempt is burned and has to be restarted.
_MAX_CODE_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class PendingOnboarding:
    step: OnboardingStep
    email: str | None = None
    code_hash: str | None = None
    attempts: int = 0


class SurfaceOnboardingStore:
    """One pending signup per sender, or none."""

    def __init__(
        self, *, redis_url: str | None = None, ttl_seconds: int | None = None
    ) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._ttl_seconds = (
            ttl_seconds or surface_settings.surface_onboarding_ttl_seconds
        )
        self._redis: Redis | None = None
        self._lock = asyncio.Lock()

    async def _get_redis(self) -> Redis:
        if self._redis is not None:
            return self._redis
        async with self._lock:
            if self._redis is None:
                self._redis = get_redis(url=self._redis_url)
        return self._redis

    @staticmethod
    def _key(*, platform: str, sender_external_user_id: str) -> str:
        return f"agent_surfaces:onboarding:{platform.lower()}:{sender_external_user_id}"

    @staticmethod
    def hash_code(code: str) -> str:
        return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()

    async def get(
        self, *, platform: str, sender_external_user_id: str
    ) -> PendingOnboarding | None:
        redis = await self._get_redis()
        raw = await redis.get(
            self._key(
                platform=platform, sender_external_user_id=sender_external_user_id
            )
        )
        if not raw:
            return None
        try:
            fields = json.loads(raw)
        except ValueError:
            return None
        step = fields.get("step")
        if step not in ("awaiting_email", "awaiting_code"):
            return None
        return PendingOnboarding(
            step=step,
            email=fields.get("email"),
            code_hash=fields.get("code_hash"),
            attempts=int(fields.get("attempts") or 0),
        )

    async def put(
        self,
        *,
        platform: str,
        sender_external_user_id: str,
        pending: PendingOnboarding,
    ) -> None:
        """Write the state, restarting the clock.

        The TTL is refreshed on every step deliberately: the thirty minutes is
        meant to be thirty minutes of silence, not thirty minutes total, so
        somebody typing their address slowly is not cut off mid-exchange.
        """
        redis = await self._get_redis()
        await redis.set(
            self._key(
                platform=platform, sender_external_user_id=sender_external_user_id
            ),
            json.dumps(
                {
                    "step": pending.step,
                    "email": pending.email,
                    "code_hash": pending.code_hash,
                    "attempts": pending.attempts,
                }
            ),
            ex=self._ttl_seconds,
        )

    async def clear(self, *, platform: str, sender_external_user_id: str) -> None:
        redis = await self._get_redis()
        await redis.delete(
            self._key(
                platform=platform, sender_external_user_id=sender_external_user_id
            )
        )

    async def close(self) -> None:
        # Shared process-wide; disposal is close_redis_clients()'s job.
        self._redis = None


def code_attempts_exhausted(attempts: int) -> bool:
    """Whether this many wrong codes has burned the attempt."""
    return attempts >= _MAX_CODE_ATTEMPTS


_onboarding_store: SurfaceOnboardingStore | None = None


def get_surface_onboarding_store() -> SurfaceOnboardingStore:
    global _onboarding_store
    if _onboarding_store is None:
        _onboarding_store = SurfaceOnboardingStore()
    return _onboarding_store


async def close_surface_onboarding_store() -> None:
    global _onboarding_store
    if _onboarding_store is None:
        return
    store = _onboarding_store
    _onboarding_store = None
    await store.close()
