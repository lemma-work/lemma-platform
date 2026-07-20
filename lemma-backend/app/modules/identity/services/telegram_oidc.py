from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, cast
from urllib.parse import urlencode, urlparse
from uuid import UUID

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select

from app.core.config import reveal_secret, settings
from app.core.infrastructure.db.session import async_session_maker
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.user_cache import get_user_cache


TelegramPurpose = Literal["signin", "verify_mobile"]


class TelegramOIDCError(ValueError):
    pass


@dataclass(frozen=True)
class TelegramTransaction:
    state: str
    nonce: str
    code_verifier: str
    purpose: TelegramPurpose
    return_to: str
    user_id: str | None


def normalize_e164(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    if not 8 <= len(digits) <= 15 or digits.startswith("0"):
        raise TelegramOIDCError("Telegram did not return a valid mobile number")
    return f"+{digits}"


def safe_return_to(value: str | None) -> str:
    fallback = settings.auth_frontend_url.rstrip("/") + "/"
    if not value:
        return fallback
    if value.startswith("/") and not value.startswith("//"):
        return settings.auth_frontend_url.rstrip("/") + value
    try:
        parsed = urlparse(value)
    except ValueError:
        return fallback
    allowed = {
        (
            urlparse(settings.auth_frontend_url).scheme,
            urlparse(settings.auth_frontend_url).netloc,
        ),
        (
            urlparse(settings.frontend_url).scheme,
            urlparse(settings.frontend_url).netloc,
        ),
    }
    if (parsed.scheme, parsed.netloc) not in allowed:
        return fallback
    return value


class TelegramOIDCService:
    def __init__(self, redis_url: str | None = None):
        self._redis = Redis.from_url(
            redis_url or settings.redis_url,
            decode_responses=True,
        )

    @staticmethod
    def _transaction_key(state: str) -> str:
        return f"identity:telegram:oidc:{hashlib.sha256(state.encode()).hexdigest()}"

    async def start(
        self,
        *,
        purpose: TelegramPurpose,
        return_to: str | None,
        user_id: UUID | None,
    ) -> str:
        if not settings.is_telegram_oidc_configured():
            raise TelegramOIDCError("Telegram login is not configured")
        if purpose == "verify_mobile" and user_id is None:
            raise TelegramOIDCError("A verified Lemma session is required")
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        transaction = {
            "state": state,
            "nonce": nonce,
            "code_verifier": verifier,
            "purpose": purpose,
            "return_to": safe_return_to(return_to),
            "user_id": str(user_id) if user_id else None,
        }
        try:
            await self._redis.set(
                self._transaction_key(state),
                json.dumps(transaction),
                ex=300,
                nx=True,
            )
        except RedisError as exc:
            raise TelegramOIDCError(
                "Telegram login is temporarily unavailable"
            ) from exc
        query = urlencode(
            {
                "client_id": settings.telegram_oidc_client_id,
                "redirect_uri": settings.telegram_oidc_redirect_uri,
                "response_type": "code",
                "scope": "openid profile phone",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{settings.telegram_oidc_authorization_endpoint}?{query}"

    async def consume(self, state: str) -> TelegramTransaction:
        try:
            raw = await self._redis.getdel(self._transaction_key(state))
        except RedisError as exc:
            raise TelegramOIDCError(
                "Telegram login is temporarily unavailable"
            ) from exc
        if not raw:
            raise TelegramOIDCError(
                "Telegram login request expired or was already used"
            )
        try:
            return TelegramTransaction(**json.loads(raw))
        except (TypeError, json.JSONDecodeError) as exc:
            raise TelegramOIDCError("Telegram login request is invalid") from exc

    @staticmethod
    def _configured_credentials() -> tuple[str, str, str]:
        client_id = settings.telegram_oidc_client_id
        client_secret = reveal_secret(settings.telegram_oidc_client_secret)
        redirect_uri = settings.telegram_oidc_redirect_uri
        if not client_id or not client_secret or not redirect_uri:
            raise TelegramOIDCError("Telegram login is not configured")
        return client_id, client_secret, redirect_uri

    @staticmethod
    async def _exchange_code(
        client: httpx.AsyncClient,
        *,
        code: str,
        transaction: TelegramTransaction,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> str:
        response = await client.post(
            settings.telegram_oidc_token_endpoint,
            auth=(client_id, client_secret),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": transaction.code_verifier,
            },
        )
        if response.status_code != 200:
            raise TelegramOIDCError("Telegram rejected the login request")
        id_token = str(response.json().get("id_token") or "")
        if not id_token:
            raise TelegramOIDCError("Telegram did not return an identity token")
        return id_token

    async def _load_jwks(self, client: httpx.AsyncClient) -> dict[str, Any]:
        try:
            cached = await self._redis.get("identity:telegram:jwks")
        except RedisError:
            cached = None
        if cached:
            return cast(dict[str, Any], json.loads(cached))

        response = await client.get(settings.telegram_oidc_jwks_uri)
        if response.status_code != 200:
            raise TelegramOIDCError("Telegram verification keys are unavailable")
        jwks = cast(dict[str, Any], response.json())
        try:
            await self._redis.set("identity:telegram:jwks", json.dumps(jwks), ex=600)
        except RedisError:
            # JWKS caching is an optimization; fresh keys already validated this
            # request, so a cache outage must not turn a valid login into a 5xx.
            pass
        return jwks

    @staticmethod
    def _decode_id_token(
        id_token: str, jwks: dict[str, Any], client_id: str
    ) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
            if header.get("alg") != "RS256":
                raise TelegramOIDCError("Unexpected Telegram signing algorithm")
            matching = next(
                key
                for key in jwks.get("keys", [])
                if key.get("kid") == header.get("kid")
            )
            signing_key = RSAAlgorithm.from_jwk(json.dumps(matching))
            return cast(
                dict[str, Any],
                jwt.decode(
                    id_token,
                    cast(Any, signing_key),
                    algorithms=["RS256"],
                    audience=client_id,
                    issuer=settings.telegram_oidc_issuer,
                    options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
                ),
            )
        except StopIteration as exc:
            raise TelegramOIDCError("Telegram signing key was not found") from exc
        except jwt.PyJWTError as exc:
            raise TelegramOIDCError("Telegram identity token is invalid") from exc

    @staticmethod
    def _validate_claims(
        claims: dict[str, Any], transaction: TelegramTransaction
    ) -> dict[str, Any]:
        if not secrets.compare_digest(
            str(claims.get("nonce") or ""), transaction.nonce
        ):
            raise TelegramOIDCError("Telegram identity token nonce is invalid")
        if claims.get("phone_number_verified") is not True:
            raise TelegramOIDCError("Telegram did not verify the mobile number")
        claims["phone_number"] = normalize_e164(str(claims.get("phone_number") or ""))
        return claims

    async def exchange_and_validate(
        self,
        *,
        code: str,
        transaction: TelegramTransaction,
    ) -> dict[str, Any]:
        client_id, client_secret, redirect_uri = self._configured_credentials()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                id_token = await self._exchange_code(
                    client,
                    code=code,
                    transaction=transaction,
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                )
                jwks = await self._load_jwks(client)
        except TelegramOIDCError:
            raise
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise TelegramOIDCError(
                "Telegram login is temporarily unavailable"
            ) from exc
        claims = self._decode_id_token(id_token, jwks, client_id)
        return self._validate_claims(claims, transaction)

    async def find_signin_user(self, phone_number: str) -> User:
        async with async_session_maker() as session:
            users = list(
                (
                    await session.execute(
                        select(User)
                        .where(
                            User.mobile_number == phone_number,
                            User.mobile_verified_at.isnot(None),
                            User.is_active.is_(True),
                            User.is_deleted.is_(False),
                            User.is_verified.is_(True),
                        )
                        .limit(2)
                    )
                ).scalars()
            )
        if len(users) != 1:
            raise TelegramOIDCError(
                "Sign in normally and verify this mobile number in account settings"
            )
        return users[0]

    async def verify_mobile(self, user_id: UUID, phone_number: str) -> None:
        async with async_session_maker() as session:
            user = await session.get(User, user_id)
            if user is None or not user.is_active or not user.is_verified:
                raise TelegramOIDCError("A verified Lemma session is required")
            owner = await session.scalar(
                select(User.id).where(
                    User.mobile_number == phone_number,
                    User.mobile_verified_at.isnot(None),
                    User.id != user_id,
                )
            )
            if owner is not None:
                raise TelegramOIDCError("This mobile number is already in use")
            user.mobile_number = phone_number
            user.mobile_verified_at = datetime.now(timezone.utc)
            await session.commit()
        await get_user_cache().invalidate(user_id)

    async def close(self) -> None:
        await self._redis.aclose()


_service: TelegramOIDCService | None = None


def get_telegram_oidc_service() -> TelegramOIDCService:
    global _service
    if _service is None:
        _service = TelegramOIDCService()
    return _service


async def close_telegram_oidc_store() -> None:
    global _service
    if _service is None:
        return
    current = _service
    _service = None
    await current.close()
