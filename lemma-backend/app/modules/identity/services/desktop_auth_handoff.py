"""Short-lived, one-time handoffs from browser auth to Lemma Desktop.

The desktop webview creates a request with a PKCE-style SHA-256 challenge and
keeps the verifier private. The system browser completes the normal Lemma login
and marks the request with the authenticated user. Only the original webview
can redeem that request because redemption requires the verifier.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass
from uuid import UUID

from redis.asyncio import Redis

from app.core.config import settings


_KEY_PREFIX = "identity:desktop-auth"
_RATE_KEY_PREFIX = "identity:desktop-auth-rate"
_DEFAULT_TTL_SECONDS = 5 * 60

_COMPLETE_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  return {'missing'}
end
local status = redis.call('HGET', KEYS[1], 'status')
if status == 'complete' then
  local completed_user_id = redis.call('HGET', KEYS[1], 'user_id')
  if completed_user_id == ARGV[1] then
    return {'complete'}
  end
  return {'conflict'}
end
if status ~= 'pending' then
  return {'conflict'}
end
redis.call('HSET', KEYS[1], 'status', 'complete', 'user_id', ARGV[1])
return {'complete'}
"""

_RATE_LIMIT_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {count, ttl}
"""

_CONSUME_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  return {'missing'}
end
local challenge = redis.call('HGET', KEYS[1], 'challenge')
if challenge ~= ARGV[1] then
  return {'forbidden'}
end
local status = redis.call('HGET', KEYS[1], 'status')
if status ~= 'complete' then
  return {'pending'}
end
local user_id = redis.call('HGET', KEYS[1], 'user_id')
redis.call('DEL', KEYS[1])
return {'complete', user_id}
"""


class DesktopAuthRequestNotFound(Exception):
    """The handoff request does not exist or has expired."""


class DesktopAuthVerifierRejected(Exception):
    """The verifier does not match the request's challenge."""


class DesktopAuthRequestPending(Exception):
    """The browser has not completed authentication yet."""


class DesktopAuthCompletionConflict(Exception):
    """A different user already completed the handoff request."""


class DesktopAuthRateLimitExceeded(Exception):
    """The client created too many handoff requests."""

    def __init__(self, retry_after_seconds: int):
        super().__init__("Desktop auth request rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class DesktopAuthRequest:
    request_id: str
    expires_in_seconds: int


def challenge_for_verifier(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class DesktopAuthHandoffStore:
    def __init__(
        self,
        redis_url: str | None = None,
        *,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        create_limit: int | None = None,
        create_window_seconds: int | None = None,
    ):
        self._redis_url = redis_url or settings.redis_url
        self._ttl_seconds = ttl_seconds
        self._create_limit = (
            settings.desktop_auth_create_limit
            if create_limit is None
            else max(0, create_limit)
        )
        self._create_window_seconds = (
            settings.desktop_auth_create_window_seconds
            if create_window_seconds is None
            else max(1, create_window_seconds)
        )
        self._redis: Redis | None = None
        self._lock = asyncio.Lock()

    async def _get_redis(self) -> Redis:
        if self._redis is not None:
            return self._redis
        async with self._lock:
            if self._redis is None:
                self._redis = Redis.from_url(
                    self._redis_url,
                    decode_responses=True,
                )
        return self._redis

    @staticmethod
    def _key(request_id: str) -> str:
        return f"{_KEY_PREFIX}:{request_id}"

    @staticmethod
    def _rate_key(client_key: str) -> str:
        digest = hashlib.sha256(client_key.encode("utf-8")).hexdigest()
        return f"{_RATE_KEY_PREFIX}:{digest}"

    async def _check_create_rate_limit(self, redis: Redis, client_key: str) -> None:
        if self._create_limit == 0:
            return
        result = await redis.eval(
            _RATE_LIMIT_LUA,
            1,
            self._rate_key(client_key),
            self._create_window_seconds,
        )
        count = int(result[0]) if result else 1
        retry_after = max(1, int(result[1]) if result and len(result) > 1 else 1)
        if count > self._create_limit:
            raise DesktopAuthRateLimitExceeded(retry_after)

    async def create(self, challenge: str, *, client_key: str) -> DesktopAuthRequest:
        redis = await self._get_redis()
        await self._check_create_rate_limit(redis, client_key)
        request_id = secrets.token_urlsafe(24)
        key = self._key(request_id)
        async with redis.pipeline(transaction=True) as pipe:
            pipe.hset(
                key,
                mapping={
                    "challenge": challenge,
                    "status": "pending",
                },
            )
            pipe.expire(key, self._ttl_seconds)
            await pipe.execute()
        return DesktopAuthRequest(
            request_id=request_id,
            expires_in_seconds=self._ttl_seconds,
        )

    async def complete(self, request_id: str, user_id: UUID) -> None:
        redis = await self._get_redis()
        result = await redis.eval(
            _COMPLETE_LUA,
            1,
            self._key(request_id),
            str(user_id),
        )
        status = result[0] if result else "missing"
        if status == "missing":
            raise DesktopAuthRequestNotFound(request_id)
        if status == "conflict":
            raise DesktopAuthCompletionConflict(request_id)

    async def consume(self, request_id: str, verifier: str) -> UUID:
        redis = await self._get_redis()
        result = await redis.eval(
            _CONSUME_LUA,
            1,
            self._key(request_id),
            challenge_for_verifier(verifier),
        )
        status = result[0] if result else "missing"
        if status == "missing":
            raise DesktopAuthRequestNotFound(request_id)
        if status == "forbidden":
            raise DesktopAuthVerifierRejected(request_id)
        if status == "pending":
            raise DesktopAuthRequestPending(request_id)
        return UUID(str(result[1]))

    async def close(self) -> None:
        if self._redis is None:
            return
        redis = self._redis
        self._redis = None
        if hasattr(redis, "aclose"):
            await redis.aclose()
        else:
            await redis.close()


_store: DesktopAuthHandoffStore | None = None


def get_desktop_auth_handoff_store() -> DesktopAuthHandoffStore:
    global _store
    if _store is None:
        _store = DesktopAuthHandoffStore()
    return _store
