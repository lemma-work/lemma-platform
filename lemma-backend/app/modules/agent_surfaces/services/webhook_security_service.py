from __future__ import annotations

import hmac
import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from app.core.authorization.scope import uow_scope
from app.core.config import settings
from app.core.domain.errors import DomainError
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.core.webhooks.signatures import (
    hex_digest_signature_matches,
    shared_secret_matches,
    slack_signature_matches,
    svix_signature_matches,
    svix_signing_key,
    timestamp_within_skew,
)
from app.modules.agent_surfaces.config import (
    resolve_resend_inbound_secret,
    surface_settings,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)

if TYPE_CHECKING:
    from app.modules.agent_surfaces.services.credential_resolver import (
        SurfaceCredentialResolver,
    )

logger = get_logger(__name__)
# The signature schemes themselves live in `app.core.webhooks.signatures` --
# Slack's versioned basestring, Meta/WhatsApp's `sha256=` digest, Telegram's
# shared secret and Svix's base64 HMAC. What stays here is which credential each
# platform is checked against, and what a failure means: this module has its own
# error type and distinguishes a misconfiguration (503) from a bad delivery.
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


@dataclass(frozen=True, slots=True)
class SlackWebhookVerificationCandidate:
    app_id: str
    signing_secret: str
    receiver_surface_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class VerifiedSlackIngress:
    app_id: str
    receiver_surface_ids: tuple[UUID, ...]


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
    """Verify that an inbound webhook really came from the platform it claims.

    Takes a way to *open* a unit of work rather than an open one. Signature
    verification needs a per-workspace secret from the database, but the routes
    that call it are inbound webhook endpoints -- the request rate belongs to
    whoever is sending -- so holding a pooled connection for the whole request
    to read one secret is the worst trade available. Each lookup opens its own
    short scope instead.

    ``resolver_factory`` is ``None`` in the tests that only exercise signature
    arithmetic; those paths fall back to the deployment-wide secret exactly as
    they did when the resolver itself was optional.
    """

    def __init__(
        self,
        *,
        uow_factory: Any = None,
        resolver_factory: Callable[[Any], SurfaceCredentialResolver] | None = None,
    ):
        self._uow_factory = uow_factory
        self._resolver_factory = resolver_factory

    @asynccontextmanager
    async def _resolver(self):
        """A credential resolver on its own short-lived session."""
        if self._resolver_factory is None or self._uow_factory is None:
            yield None
            return
        async with uow_scope(self._uow_factory) as uow:
            yield self._resolver_factory(uow)

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
        if surface.surface_type is SurfacePlatform.WHATSAPP:
            app_secret, _ = await self._resolve_whatsapp_secrets(surface)
            self._verify_whatsapp_signature(
                headers=headers,
                raw_body=raw_body,
                app_secret=app_secret,
            )
            return
        if surface.surface_type is SurfacePlatform.SLACK:
            signing_secret = (
                surface.webhook_secret or surface_settings.slack_signing_secret
            )
            async with self._resolver() as resolver:
                if resolver is not None:
                    credentials = await resolver.slack_webhook_credentials(surface)
                    signing_secret = credentials.signing_secret
            self._verify_slack_signature(
                headers=headers,
                raw_body=raw_body,
                signing_secret=signing_secret,
            )
            return
        await self.verify_platform_request(
            platform=surface.surface_type.value,
            headers=headers,
            raw_body=raw_body,
        )

    def verify_slack_request(
        self,
        *,
        headers: dict[str, str],
        raw_body: bytes,
        api_app_id: str | None,
        candidates: list[SlackWebhookVerificationCandidate],
    ) -> VerifiedSlackIngress:
        normalized_app_id = str(api_app_id or "").strip()
        if not self.verification_enabled():
            return VerifiedSlackIngress(
                app_id=normalized_app_id or "security-disabled",
                receiver_surface_ids=tuple(
                    dict.fromkeys(
                        surface_id
                        for candidate in candidates
                        for surface_id in candidate.receiver_surface_ids
                    )
                ),
            )
        # The signature is the authentication: only the app holding the secret
        # can produce one. The app id merely says *which* secret to try first,
        # which matters when an org runs its own Slack app alongside ours in the
        # same workspace. So a request that does not name an app is not
        # rejected — every secret bound to the workspace is tried, and one has
        # to verify. Narrowing by app id when we have it keeps the common case
        # to a single HMAC and keeps the reported app honest.
        matching = (
            [
                candidate
                for candidate in candidates
                if hmac.compare_digest(candidate.app_id, normalized_app_id)
            ]
            if normalized_app_id
            else list(candidates)
        )
        if not matching and normalized_app_id:
            # Named an app we hold no secret for. Falling back to every other
            # candidate would be answering for an app we were not addressed as.
            raise SurfaceWebhookAuthenticationError(
                "Slack request targets an unknown app for this workspace"
            )
        if not matching:
            raise SurfaceWebhookAuthenticationError(
                "No Slack app is configured for this workspace"
            )
        last_error: SurfaceWebhookAuthenticationError | None = None
        for candidate in matching:
            try:
                self._verify_slack_signature(
                    headers=headers,
                    raw_body=raw_body,
                    signing_secret=candidate.signing_secret,
                )
                return VerifiedSlackIngress(
                    # Unnamed request: report the app whose secret verified it,
                    # so downstream never sees an empty app id.
                    app_id=normalized_app_id or candidate.app_id,
                    receiver_surface_ids=candidate.receiver_surface_ids,
                )
            except SurfaceWebhookAuthenticationError as exc:
                last_error = exc
        raise last_error or SurfaceWebhookAuthenticationError(
            "Slack signature did not match the targeted app for this workspace"
        )

    async def verify_resend_request(
        self,
        *,
        headers: dict[str, str],
        raw_body: bytes,
    ) -> None:
        """Verify a Resend (Svix) inbound webhook signature.

        Resend does not go through ``assert_platform_request_allowed`` (that path
        only covers the four chat platforms with a shared webhook), so the
        controller calls this directly before enqueuing the inbound email.

        Reads ``RESEND_WEBHOOK_SECRET``, the single secret for the Resend
        webhook. Svix issues one per *endpoint*, so the only case needing a
        second variable is bounces configured as their own Resend endpoint —
        ``RESEND_BOUNCE_WEBHOOK_SECRET``, read by the bounce controller and
        defaulting to this one. Nothing overrides the inbound side on its own.
        """
        if not self.verification_enabled():
            return
        self._verify_svix_signature(
            headers=headers,
            raw_body=raw_body,
            signing_secret=resolve_resend_inbound_secret(),
        )

    def _verify_svix_signature(
        self,
        *,
        headers: dict[str, str],
        raw_body: bytes,
        signing_secret: str | None,
    ) -> None:
        if not signing_secret:
            raise SurfaceWebhookAuthenticationError(
                "Resend inbound signing secret is not configured",
                status_code=503,
            )
        svix_id = headers.get("svix-id") or headers.get("Svix-Id")
        svix_timestamp = headers.get("svix-timestamp") or headers.get("Svix-Timestamp")
        svix_signature = headers.get("svix-signature") or headers.get("Svix-Signature")
        if not svix_id or not svix_timestamp or not svix_signature:
            raise SurfaceWebhookAuthenticationError("Missing Svix signature headers")
        if not timestamp_within_skew(svix_timestamp):
            raise SurfaceWebhookAuthenticationError("Svix request timestamp is too old")
        # A secret that is not base64 is our configuration being wrong, not the
        # delivery being wrong, and 503 is what says so. The shared scheme drops
        # such a candidate instead of raising -- correct when there are several,
        # and it would turn this into an indistinguishable 401 if left to it.
        if not svix_signing_key(signing_secret):
            raise SurfaceWebhookAuthenticationError(
                "Resend inbound signing secret is malformed",
                status_code=503,
            )
        if not svix_signature_matches(
            svix_signature, svix_id, svix_timestamp, raw_body, [signing_secret]
        ):
            raise SurfaceWebhookAuthenticationError("Invalid Svix request signature")

    async def _resolve_whatsapp_secrets(
        self, surface: AgentSurfaceEntity | None
    ) -> tuple[str | None, str | None]:
        """Returns ``(app_secret, verify_token)`` to check a WhatsApp request against.

        A surface bound to a connector account (the org's own WhatsApp Business
        app) is verified against *that account's* stored ``app_secret`` /
        ``verify_token`` — never the system fallback, so a misconfigured or
        missing org credential fails closed instead of silently matching
        Lemma's own managed number. Only account-less (Lemma-managed) surfaces
        use the env-configured system credentials.
        """
        if surface is not None and surface.account_id is not None:
            try:
                async with self._resolver() as resolver:
                    if resolver is None:
                        return None, None
                    credentials = await resolver.for_account(surface.account_id)
            except Exception:
                logger.debug(
                    "agent_surfaces.webhook_security_service.could_not_resolve_whatsapp_credentials.diagnostic",
                    account_id=surface.account_id,
                    exc_info=True,
                )
                return None, None
            return credentials.get("app_secret"), credentials.get("verify_token")
        return (
            surface_settings.whatsapp_app_secret,
            surface_settings.whatsapp_verify_token,
        )

    async def resolve_whatsapp_verify_token(
        self, surface: AgentSurfaceEntity | None
    ) -> str | None:
        """The verify token to check ``hub.verify_token`` against for this surface."""
        _, verify_token = await self._resolve_whatsapp_secrets(surface)
        return verify_token

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
        # Stale-but-authentic and forged are different problems, and the messages
        # say so; the shared scheme keeps them apart for exactly that reason.
        if not timestamp_within_skew(timestamp):
            raise SurfaceWebhookAuthenticationError(
                "Slack request timestamp is too old"
            )
        if not slack_signature_matches(
            signature, timestamp, raw_body, [signing_secret]
        ):
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
        if not signature:
            raise SurfaceWebhookAuthenticationError("Missing WhatsApp signature header")
        if not hex_digest_signature_matches(signature, raw_body, [app_secret]):
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
        if not shared_secret_matches(header_secret, [webhook_secret]):
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
            surface_settings.microsoft_bot_openid_config_url
            or _BOT_FRAMEWORK_OPENID_CONFIG_URL
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
            raise SurfaceWebhookAuthenticationError(
                "Malformed Teams bearer token"
            ) from exc

        key_id = header.get("kid") or header.get("x5t")
        if not key_id:
            raise SurfaceWebhookAuthenticationError(
                "Teams bearer token is missing key id"
            )

        for key in keys:
            if key.get("kid") == key_id or key.get("x5t") == key_id:
                return RSAAlgorithm.from_jwk(json.dumps(key))

        raise SurfaceWebhookAuthenticationError("Unable to resolve Teams signing key")
