from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from app.core.config import settings
from app.core.domain.errors import DomainError
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity, SurfacePlatform
_SLACK_SIGNATURE_VERSION = "v0"
_SLACK_MAX_TIMESTAMP_SKEW_SECONDS = 60 * 5
_BOT_FRAMEWORK_OPENID_CONFIG_URL = (
    "https://login.botframework.com/v1/.well-known/openidconfiguration"
)
_BOT_FRAMEWORK_ALLOWED_ISSUERS = frozenset(
    {
        "https://api.botframework.com",
        "https://api.botframework.com/",
    }
)
_OIDC_CACHE_TTL_SECONDS = 60 * 10

# Shared Redis cache of OIDC/JWKS documents used to verify Teams webhook JWTs, so
# the metadata is fetched once across replicas. Redis unavailable -> refetch.
_oidc_cache: RedisJsonCache | None = None


def _get_oidc_cache() -> RedisJsonCache:
    global _oidc_cache
    if _oidc_cache is None or _oidc_cache._redis_url != settings.redis_url:
        _oidc_cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="surface:oidc",
            ttl_seconds=_OIDC_CACHE_TTL_SECONDS,
        )
    return _oidc_cache


class SurfaceWebhookAuthenticationError(DomainError):
    """Webhook signature / authenticity check failed.

    A ``DomainError`` so the global handler translates it automatically (no
    manual catch-and-remap in the webhook controller). ``status_code`` is
    caller-supplied (401 for bad signatures, 404 for platforms without direct
    ingress).
    """

    def __init__(self, detail: str, *, status_code: int = 401):
        super().__init__(
            detail, code="SURFACE_WEBHOOK_AUTH_FAILED", status_code=status_code
        )
        # Preserve the legacy attribute name for any existing readers.
        self.detail = detail


class SurfaceWebhookSecurityService:
    def verification_enabled(self) -> bool:
        return bool(surface_settings.surface_webhook_security_enabled)

    def assert_platform_request_allowed(self, platform: str) -> None:
        if str(platform).upper() not in {"SLACK", "TEAMS", "WHATSAPP", "TELEGRAM"}:
            raise SurfaceWebhookAuthenticationError(
                f"Platform '{platform}' does not support direct webhook ingress",
                status_code=404,
            )

    async def verify_platform_request(
        self,
        *,
        platform: str,
        headers: dict[str, str],
        raw_body: bytes,
    ) -> None:
        if not self.verification_enabled():
            return
        normalized = str(platform).upper()
        if normalized == "SLACK":
            self._verify_slack_signature(
                headers=headers,
                raw_body=raw_body,
                signing_secret=surface_settings.slack_signing_secret,
            )
            return
        if normalized == "WHATSAPP":
            self._verify_whatsapp_signature(
                headers=headers,
                raw_body=raw_body,
                app_secret=surface_settings.whatsapp_app_secret,
            )
            return
        if normalized == "TELEGRAM":
            self._verify_telegram_secret(
                headers=headers,
                webhook_secret=surface_settings.telegram_webhook_secret,
            )
            return
        if normalized == "TEAMS":
            await self._verify_teams_jwt(
                headers=headers,
                expected_app_id=surface_settings.microsoft_bot_app_id,
            )
            return
        raise SurfaceWebhookAuthenticationError(
            f"Platform '{platform}' does not support webhook verification",
            status_code=404,
        )

    async def verify_surface_request(
        self,
        *,
        surface: AgentSurfaceEntity,
        headers: dict[str, str],
        raw_body: bytes,
    ) -> None:
        if not self.verification_enabled():
            return
        if surface.surface_type is SurfacePlatform.TELEGRAM:
            self._verify_telegram_secret(
                headers=headers,
                webhook_secret=surface.webhook_secret,
            )
            return
        await self.verify_platform_request(
            platform=surface.surface_type.value,
            headers=headers,
            raw_body=raw_body,
        )

    def _verify_slack_signature(
        self,
        *,
        headers: dict[str, str],
        raw_body: bytes,
        signing_secret: str | None,
    ) -> None:
        if not signing_secret:
            raise SurfaceWebhookAuthenticationError(
                "Slack signing secret is not configured",
                status_code=503,
            )
        signature = headers.get("x-slack-signature") or headers.get("X-Slack-Signature")
        timestamp = headers.get("x-slack-request-timestamp") or headers.get(
            "X-Slack-Request-Timestamp"
        )
        if not signature or not timestamp:
            raise SurfaceWebhookAuthenticationError("Missing Slack signature headers")
        try:
            timestamp_int = int(timestamp)
        except (TypeError, ValueError) as exc:
            raise SurfaceWebhookAuthenticationError(
                "Invalid Slack request timestamp"
            ) from exc
        if abs(int(time.time()) - timestamp_int) > _SLACK_MAX_TIMESTAMP_SKEW_SECONDS:
            raise SurfaceWebhookAuthenticationError("Slack request timestamp is too old")

        basestring = (
            f"{_SLACK_SIGNATURE_VERSION}:{timestamp_int}:".encode("utf-8") + raw_body
        )
        expected = (
            f"{_SLACK_SIGNATURE_VERSION}="
            f"{hmac.new(signing_secret.encode('utf-8'), basestring, hashlib.sha256).hexdigest()}"
        )
        if not hmac.compare_digest(expected, signature):
            raise SurfaceWebhookAuthenticationError("Invalid Slack request signature")

    def _verify_whatsapp_signature(
        self,
        *,
        headers: dict[str, str],
        raw_body: bytes,
        app_secret: str | None,
    ) -> None:
        if not app_secret:
            raise SurfaceWebhookAuthenticationError(
                "WhatsApp app secret is not configured",
                status_code=503,
            )
        signature = headers.get("x-hub-signature-256") or headers.get(
            "X-Hub-Signature-256"
        )
        if not signature or not signature.startswith("sha256="):
            raise SurfaceWebhookAuthenticationError("Missing WhatsApp signature header")
        expected = "sha256=" + hmac.new(
            app_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise SurfaceWebhookAuthenticationError("Invalid WhatsApp signature")

    def _verify_telegram_secret(
        self,
        *,
        headers: dict[str, str],
        webhook_secret: str | None,
    ) -> None:
        if not webhook_secret:
            raise SurfaceWebhookAuthenticationError(
                "Telegram webhook secret is not configured",
                status_code=503,
            )
        header_secret = headers.get("x-telegram-bot-api-secret-token") or headers.get(
            "X-Telegram-Bot-Api-Secret-Token"
        )
        if not header_secret:
            raise SurfaceWebhookAuthenticationError(
                "Missing Telegram webhook secret header"
            )
        if not hmac.compare_digest(webhook_secret, header_secret):
            raise SurfaceWebhookAuthenticationError("Invalid Telegram webhook secret")

    async def _verify_teams_jwt(
        self,
        *,
        headers: dict[str, str],
        expected_app_id: str | None,
    ) -> None:
        if not expected_app_id:
            raise SurfaceWebhookAuthenticationError(
                "Teams bot app ID is not configured",
                status_code=503,
            )
        auth_header = headers.get("authorization") or headers.get("Authorization")
        if not auth_header or not auth_header.lower().startswith("bearer "):
            raise SurfaceWebhookAuthenticationError("Missing Teams bearer token")
        token = auth_header.split(" ", 1)[1].strip()
        if not token:
            raise SurfaceWebhookAuthenticationError("Missing Teams bearer token")

        openid_url = (
            surface_settings.microsoft_bot_openid_config_url or _BOT_FRAMEWORK_OPENID_CONFIG_URL
        )
        openid_config = await self._get_json_cached(openid_url)
        jwks_uri = str(openid_config.get("jwks_uri") or "").strip()
        if not jwks_uri:
            raise SurfaceWebhookAuthenticationError(
                "Teams OpenID metadata is missing jwks_uri",
                status_code=503,
            )
        jwks = await self._get_json_cached(jwks_uri)
        keys = jwks.get("keys") or []
        signing_key = self._resolve_jwt_signing_key(token, keys)
        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=expected_app_id,
                options={"verify_iss": False},
            )
        except jwt.PyJWTError as exc:
            raise SurfaceWebhookAuthenticationError(
                "Invalid Teams bearer token"
            ) from exc

        issuer = str(claims.get("iss") or "").strip()
        if issuer not in _BOT_FRAMEWORK_ALLOWED_ISSUERS:
            raise SurfaceWebhookAuthenticationError("Invalid Teams token issuer")

    async def _get_json_cached(self, url: str) -> dict[str, Any]:
        cache = _get_oidc_cache()
        try:
            cached = await cache.get_json(url)
        except Exception:
            cached = None
        if cached is not None:
            return cached

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            if response.status_code != 200:
                raise SurfaceWebhookAuthenticationError(
                    f"Failed to load Teams verification metadata from {url}",
                    status_code=503,
                )
            payload = response.json()
        if not isinstance(payload, dict):
            raise SurfaceWebhookAuthenticationError(
                f"Invalid Teams verification metadata from {url}",
                status_code=503,
            )
        try:
            await cache.set_json(url, payload, ttl_seconds=_OIDC_CACHE_TTL_SECONDS)
        except Exception:
            pass
        return payload

    def _resolve_jwt_signing_key(self, token: str, keys: list[dict[str, Any]]) -> Any:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise SurfaceWebhookAuthenticationError("Malformed Teams bearer token") from exc

        key_id = header.get("kid") or header.get("x5t")
        if not key_id:
            raise SurfaceWebhookAuthenticationError("Teams bearer token is missing key id")

        for key in keys:
            if key.get("kid") == key_id or key.get("x5t") == key_id:
                return RSAAlgorithm.from_jwk(json.dumps(key))

        raise SurfaceWebhookAuthenticationError("Unable to resolve Teams signing key")
