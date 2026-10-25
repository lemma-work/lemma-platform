from __future__ import annotations

from app.modules.agent_surfaces.platforms.platform_capabilities import (
    get_platform_capabilities,
)

from app.modules.agent_surfaces.domain.credentials import WhatsAppCredentials

from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.config import reveal_secret
from app.core.authorization.scope import uow_scope
from app.core.log.log import get_logger
from app.core.webhooks.signatures import (
    constant_time_equals,
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
    surface_webhook_verification_enabled,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import SurfaceWebhookAuthenticationError
from app.modules.agent_surfaces.services.teams_jwt_verifier import TeamsJwtVerifier

__all__ = [
    "SlackWebhookVerificationCandidate",
    "SurfaceWebhookAuthenticationError",
    "SurfaceWebhookSecurityService",
    "VerifiedSlackIngress",
]

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


@dataclass(frozen=True, slots=True)
class SlackWebhookVerificationCandidate:
    app_id: str
    signing_secret: str
    receiver_surface_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class VerifiedSlackIngress:
    app_id: str
    receiver_surface_ids: tuple[UUID, ...]


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
        teams_verifier: TeamsJwtVerifier | None = None,
    ):
        self._uow_factory = uow_factory
        self._resolver_factory = resolver_factory
        self._teams_verifier = teams_verifier or TeamsJwtVerifier()

    @asynccontextmanager
    async def _resolver(self):
        """A credential resolver on its own short-lived session."""
        if self._resolver_factory is None or self._uow_factory is None:
            yield None
            return
        async with uow_scope(self._uow_factory) as uow:
            yield self._resolver_factory(uow)

    def verification_enabled(self) -> bool:
        return surface_webhook_verification_enabled()

    def assert_platform_request_allowed(self, platform: str) -> None:
        # Every chat platform; mail arrives on its own provider's route.
        capabilities = get_platform_capabilities(str(platform).upper())
        if capabilities is None or capabilities.is_email:
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
                signing_secret=reveal_secret(surface_settings.slack_signing_secret),
            )
            return
        if normalized == "WHATSAPP":
            self._verify_whatsapp_signature(
                headers=headers,
                raw_body=raw_body,
                app_secret=reveal_secret(surface_settings.whatsapp_app_secret),
            )
            return
        if normalized == "TELEGRAM":
            self._verify_telegram_secret(
                headers=headers,
                webhook_secret=reveal_secret(surface_settings.telegram_webhook_secret),
            )
            return
        if normalized == "TEAMS":
            await self._teams_verifier.verify(
                headers=headers,
                raw_body=raw_body,
                expected_app_id=surface_settings.microsoft_bot_app_id,
            )
            return
        raise SurfaceWebhookAuthenticationError(
            f"Platform '{platform}' does not support webhook verification",
            status_code=404,
        )

    def verify_whatsapp_app_secret(
        self,
        *,
        headers: dict[str, str],
        raw_body: bytes,
        app_secret: str | None,
    ) -> None:
        """Check ``X-Hub-Signature-256`` against one caller-chosen app secret.

        The two verifiers above each *derive* the secret: from settings for the
        platform endpoint, from the surface for the surface endpoint. A pooled
        WhatsApp number has neither -- it is named by the URL it arrived on and
        its secret comes from its own row -- so the caller is the only thing
        that knows which secret is meant, and this lets it say so without
        reaching for a private method or restating Meta's signature scheme.

        Still honours the deployment-wide verification switch, so a developer
        machine behaves here exactly as it does on every other webhook route.
        """
        if not self.verification_enabled():
            return
        self._verify_whatsapp_signature(
            headers=headers,
            raw_body=raw_body,
            app_secret=app_secret,
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
                webhook_secret=reveal_secret(surface.webhook_secret),
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
            signing_secret = reveal_secret(surface.webhook_secret) or reveal_secret(
                surface_settings.slack_signing_secret
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
                if constant_time_equals(normalized_app_id, candidate.app_id)
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
        if surface is not None and surface.pod_connector_slug is not None:
            try:
                async with self._resolver() as resolver:
                    if resolver is None:
                        return None, None
                    credentials = WhatsAppCredentials.of(
                        await resolver.for_surface(surface)
                    )
            except Exception as exc:
                # The request is refused as unverifiable either way; this is
                # what tells a broken connector account apart from a setting
                # nobody configured.
                logger.warning(
                    "agent_surfaces.webhook_security_service.whatsapp_credentials_unavailable.degraded",
                    surface_id=str(surface.id),
                    error_type=type(exc).__name__,
                    exc_info=exc,
                )
                return None, None
            return (
                reveal_secret(credentials.app_secret),
                reveal_secret(credentials.verify_token),
            )
        return (
            reveal_secret(surface_settings.whatsapp_app_secret),
            reveal_secret(surface_settings.whatsapp_verify_token),
        )

    async def resolve_whatsapp_verify_token(
        self, surface: AgentSurfaceEntity | None
    ) -> str | None:
        """The verify token to check ``hub.verify_token`` against for this surface."""
        _, verify_token = await self._resolve_whatsapp_secrets(surface)
        return verify_token

    def verify_slack_handshake(self, *, headers: dict[str, str]) -> None:
        """Check what can be checked of Slack's ``url_verification`` request.

        Slack signs the handshake like any other request, but its body names
        no workspace and no app -- it is ``{token, challenge, type}`` -- so
        there is no signing secret to select. Checking it against the
        deployment's own would refuse every org pointing its own Slack app at
        this shared endpoint. What is left is that it carries Slack's headers
        and a timestamp inside the replay window; the caller answers with the
        challenge alone, bounded, and acts on nothing else in the body.
        """
        if not self.verification_enabled():
            return
        signature = headers.get("x-slack-signature") or headers.get("X-Slack-Signature")
        timestamp = headers.get("x-slack-request-timestamp") or headers.get(
            "X-Slack-Request-Timestamp"
        )
        if not signature or not timestamp:
            raise SurfaceWebhookAuthenticationError("Missing Slack signature headers")
        if not timestamp_within_skew(timestamp):
            raise SurfaceWebhookAuthenticationError(
                "Slack request timestamp is too old"
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
