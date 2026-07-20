from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import reveal_secret, settings
from app.core.log.log import get_logger


logger = get_logger(__name__)
_RATE_LUA = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {current, redis.call('TTL', KEYS[1])}
"""


@dataclass(frozen=True)
class RateLimitExceeded(Exception):
    retry_after_seconds: int


class AltchaRejected(ValueError):
    pass


class AuthAbuseStore:
    def __init__(self, redis_url: str | None = None):
        self._redis = Redis.from_url(
            redis_url or settings.redis_url,
            decode_responses=True,
        )

    @staticmethod
    def digest(value: str) -> str:
        key = reveal_secret(settings.auth_altcha_hmac_key) or "lemma-auth-key"
        return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()

    async def enforce(self, key: str, *, limit: int, window_seconds: int) -> None:
        if not settings.auth_abuse_protection_enabled:
            return
        try:
            result = await self._redis.eval(  # type: ignore[reportGeneralTypeIssues,reportArgumentType]
                _RATE_LUA, 1, key, str(window_seconds)
            )
        except RedisError:
            logger.error("identity.auth_abuse.rate_limit_unavailable", exc_info=True)
            return
        count = int(result[0])
        retry_after = max(1, int(result[1]))
        if count > limit:
            raise RateLimitExceeded(retry_after)

    async def count(self, key: str) -> int:
        try:
            value = await self._redis.get(key)
        except RedisError:
            return 0
        return int(value or 0)

    async def clear(self, *keys: str) -> None:
        if not keys:
            return
        try:
            await self._redis.delete(*keys)
        except RedisError:
            logger.warning("identity.auth_abuse.counter_clear_failed")

    async def issue_altcha(self, purpose: str) -> dict[str, Any]:
        if not settings.auth_altcha_enabled:
            return {"enabled": False}
        key = reveal_secret(settings.auth_altcha_hmac_key)
        if not key:
            raise RuntimeError(
                "AUTH_ALTCHA_HMAC_KEY is required when ALTCHA is enabled"
            )
        maximum = settings.auth_altcha_max_number
        salt = secrets.token_hex(16)
        number = secrets.randbelow(maximum + 1)
        challenge = hashlib.sha256(f"{salt}{number}".encode()).hexdigest()
        signature = hmac.new(
            key.encode(), challenge.encode(), hashlib.sha256
        ).hexdigest()
        record = json.dumps(
            {
                "purpose": purpose,
                "signature": signature,
                "expires_at": int(time.time()) + 300,
            }
        )
        try:
            await self._redis.set(
                f"identity:altcha:{self.digest(challenge)}",
                record,
                ex=300,
                nx=True,
            )
        except RedisError as exc:
            raise RuntimeError("ALTCHA challenge store is unavailable") from exc
        return {
            "enabled": True,
            "algorithm": "SHA-256",
            "challenge": challenge,
            "maxnumber": maximum,
            "salt": salt,
            "signature": signature,
        }

    async def verify_altcha(self, encoded_payload: str | None, *, purpose: str) -> None:
        if not settings.auth_altcha_enabled:
            return
        if not encoded_payload:
            raise AltchaRejected("Missing proof-of-work")
        try:
            padded = encoded_payload + "=" * (-len(encoded_payload) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode())
            algorithm = payload["algorithm"]
            challenge = str(payload["challenge"])
            number = int(payload["number"])
            salt = str(payload["salt"])
            signature = str(payload["signature"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AltchaRejected("Malformed proof-of-work") from exc
        if algorithm != "SHA-256" or not 0 <= number <= settings.auth_altcha_max_number:
            raise AltchaRejected("Invalid proof-of-work parameters")
        key = reveal_secret(settings.auth_altcha_hmac_key)
        if not key:
            raise AltchaRejected("Proof-of-work is unavailable")
        expected_signature = hmac.new(
            key.encode(), challenge.encode(), hashlib.sha256
        ).hexdigest()
        expected_challenge = hashlib.sha256(f"{salt}{number}".encode()).hexdigest()
        if not hmac.compare_digest(
            signature, expected_signature
        ) or not hmac.compare_digest(challenge, expected_challenge):
            raise AltchaRejected("Invalid proof-of-work")
        try:
            raw = await self._redis.getdel(f"identity:altcha:{self.digest(challenge)}")
        except RedisError as exc:
            raise AltchaRejected("Proof-of-work store is unavailable") from exc
        if not raw:
            raise AltchaRejected("Proof-of-work expired or already used")
        record = json.loads(raw)
        if (
            record.get("purpose") != purpose
            or record.get("signature") != signature
            or int(record.get("expires_at", 0)) < int(time.time())
        ):
            raise AltchaRejected("Proof-of-work expired or has the wrong purpose")

    async def close(self) -> None:
        await self._redis.aclose()


def client_ip(scope: Any) -> str:
    peer = str((scope.get("client") or ("unknown", 0))[0])
    trusted = set(settings.auth_trusted_proxy_ips)
    if peer not in trusted:
        return peer
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }
    forwarded = headers.get("forwarded", "")
    candidate = ""
    if forwarded:
        first = forwarded.split(",", 1)[0]
        for part in first.split(";"):
            if part.strip().lower().startswith("for="):
                candidate = part.split("=", 1)[1].strip().strip('"').strip("[]")
                break
    if not candidate:
        candidate = headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    try:
        return str(ip_address(candidate)) if candidate else peer
    except ValueError:
        return peer


_store: AuthAbuseStore | None = None


def get_auth_abuse_store() -> AuthAbuseStore:
    global _store
    if _store is None:
        _store = AuthAbuseStore()
    return _store


async def close_auth_abuse_store() -> None:
    global _store
    if _store is None:
        return
    current = _store
    _store = None
    await current.close()
