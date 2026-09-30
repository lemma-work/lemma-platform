"""Atomic, short-lived browser handoffs and opaque app asset sessions."""

import hashlib
import secrets
import time

from pydantic import ValidationError
from redis.asyncio import Redis

from app.core.infrastructure.redis.client import get_redis
from app.modules.apps.config import apps_settings
from app.modules.apps.domain.access import (
    AppAccessCode,
    AppAccessInvalidError,
    AppAccessRateLimitedError,
    AppAccessRequest,
    AppAccessSession,
)

REQUEST_TTL_SECONDS = 300
CODE_TTL_SECONDS = 60

_RATE = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], 60) end
return count
"""

_AUTHORIZE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
if not redis.call('SET', KEYS[2], ARGV[2], 'NX', 'EX', 60) then return 0 end
redis.call('SET', KEYS[3], ARGV[3], 'EX', 60)
return 1
"""

_REDEEM = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
if redis.call('GET', KEYS[2]) ~= ARGV[2] then return 0 end
if redis.call('GET', KEYS[3]) ~= ARGV[3] then return 0 end
redis.call('SET', KEYS[4], ARGV[4], 'EX', ARGV[5])
redis.call('DEL', KEYS[1], KEYS[2], KEYS[3])
return 1
"""


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _key(kind: str, value: str) -> str:
    return f"apps:access:{kind}:{token_hash(value)}"


class AppAccessStore:
    def __init__(self, redis: Redis | None = None) -> None:
        self.redis = redis if redis is not None else get_redis()

    async def create(self, request: AppAccessRequest, *, client_key: str) -> str:
        count = await self.redis.eval(_RATE, 1, _key("rate", client_key))
        if int(count) > apps_settings.app_access_create_limit_per_minute:
            raise AppAccessRateLimitedError()
        request_id = secrets.token_urlsafe(32)
        await self.redis.set(
            _key("request", request_id),
            request.model_dump_json(),
            ex=REQUEST_TTL_SECONDS,
        )
        return request_id

    async def get_request(self, request_id: str) -> AppAccessRequest:
        raw = await self.redis.get(_key("request", request_id))
        if raw is None:
            raise AppAccessInvalidError()
        try:
            return AppAccessRequest.model_validate_json(raw)
        except ValidationError as exc:
            raise AppAccessInvalidError() from exc

    async def authorize(
        self, request_id: str, request: AppAccessRequest, session: AppAccessSession
    ) -> str:
        code = secrets.token_urlsafe(32)
        authorized = AppAccessCode(request_id=request_id, session=session)
        result = await self.redis.eval(
            _AUTHORIZE,
            3,
            _key("request", request_id),
            _key("authorized", request_id),
            _key("code", code),
            request.model_dump_json(),
            token_hash(code),
            authorized.model_dump_json(),
        )
        if not result:
            raise AppAccessInvalidError()
        return code

    async def redeem(
        self, *, request_id: str, code: str, challenge: str, binding: str, origin: str
    ) -> tuple[str, AppAccessSession]:
        request = await self.get_request(request_id)
        if (
            request.origin != origin
            or not secrets.compare_digest(request.challenge, challenge)
            or not secrets.compare_digest(request.binding_hash, token_hash(binding))
        ):
            raise AppAccessInvalidError()
        raw = await self.redis.get(_key("code", code))
        if raw is None:
            raise AppAccessInvalidError()
        try:
            authorized = AppAccessCode.model_validate_json(raw)
        except ValidationError as exc:
            raise AppAccessInvalidError() from exc
        session = authorized.session
        ttl = session.expires_at - int(time.time())
        if authorized.request_id != request_id or session.origin != origin or ttl <= 0:
            raise AppAccessInvalidError()
        token = secrets.token_urlsafe(32)
        result = await self.redis.eval(
            _REDEEM,
            4,
            _key("request", request_id),
            _key("authorized", request_id),
            _key("code", code),
            _key("session", token),
            request.model_dump_json(),
            token_hash(code),
            raw,
            session.model_dump_json(),
            ttl,
        )
        if not result:
            raise AppAccessInvalidError()
        return token, session

    async def get_session(self, token: str, *, origin: str) -> AppAccessSession:
        raw = await self.redis.get(_key("session", token))
        if raw is None:
            raise AppAccessInvalidError()
        try:
            session = AppAccessSession.model_validate_json(raw)
        except ValidationError as exc:
            raise AppAccessInvalidError() from exc
        if session.origin != origin or session.expires_at <= int(time.time()):
            raise AppAccessInvalidError()
        return session
