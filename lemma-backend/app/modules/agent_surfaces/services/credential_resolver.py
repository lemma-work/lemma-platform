"""Resolves the credentials a platform adapter needs for a surface.

Single home for the account-credential merging rules shared by the ingress
pipeline and the agent tool factory:

- Accounts whose apps manage their own long-lived secrets (bot tokens etc.)
  use stored credentials as-is; OAuth-backed apps go through the connector
  service refresh flow with a stored-credential fallback.
- Non-secret context keys (scopes, raw_response, user_data) are merged back in
  because platform adapters read identity data from them.
- WhatsApp/Telegram can run on system credentials from environment settings
  when no account is connected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.modules.agent_surfaces.config import surface_settings
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.surface_connectors import (
    SELF_MANAGED_CREDENTIAL_CONNECTOR_IDS,
)
from app.composition.surface_connectors import Account, ConnectorService
from app.modules.connectors.domain.auth_config import AuthConfigSource

logger = get_logger(__name__)

# Connectors that manage service-level credentials (no OAuth refresh flow),
# derived from the surface-connector registry so it can't drift from the
# bindings. Resend, for one, uses a static API key, not a 3-legged OAuth token —
# routing it through the OAuth refresh flow would silently drop `api_key`
# (ConnectorService's `_to_oauth_credentials` only carries access_token/
# refresh_token/token_type/expires_at/raw_response/connection_id; `api_key` isn't
# one of them, and it isn't in `_CONTEXT_KEYS` below either, so nothing rescues it).
_SELF_MANAGED_CREDENTIAL_APPS = SELF_MANAGED_CREDENTIAL_CONNECTOR_IDS

# Non-secret keys platform adapters read for identity/routing context.
_CONTEXT_KEYS = ("scope", "scopes", "api_base_url", "raw_response", "user_data")


@dataclass(frozen=True, slots=True)
class SlackWebhookCredentials:
    app_id: str | None
    signing_secret: str | None
    uses_custom_app: bool


def native_credentials(platform: str | SurfacePlatform | None) -> dict[str, Any]:
    """System credentials from environment settings (WhatsApp/Telegram only)."""
    normalized = str(platform or "").upper()
    if normalized == SurfacePlatform.WHATSAPP:
        credentials = {
            "access_token": surface_settings.whatsapp_access_token or "",
            "phone_number_id": surface_settings.whatsapp_phone_number_id or "",
            "waba_id": surface_settings.whatsapp_waba_id or "",
        }
        app_secret = surface_settings.whatsapp_app_secret
        if app_secret:
            credentials["app_secret"] = app_secret
        return credentials
    if normalized == SurfacePlatform.TELEGRAM:
        return {"bot_token": surface_settings.telegram_bot_token or ""}
    if normalized == SurfacePlatform.RESEND:
        # from_address is per-surface (the provisioned pod address); the resolver
        # injects it from surface.surface_identity_email in for_surface().
        return {
            "api_key": surface_settings.resend_api_key or "",
            "from_name": surface_settings.resend_from_name or "Lemma",
        }
    return {}


def has_native_credentials(platform: str | SurfacePlatform | None) -> bool:
    normalized = str(platform or "").upper()
    if normalized == SurfacePlatform.WHATSAPP:
        return bool(
            surface_settings.whatsapp_access_token
            and surface_settings.whatsapp_phone_number_id
        )
    if normalized == SurfacePlatform.TELEGRAM:
        return bool(surface_settings.telegram_bot_token)
    if normalized == SurfacePlatform.RESEND:
        return bool(surface_settings.resend_api_key)
    return False


class SurfaceCredentialResolver:
    def __init__(self, *, session, connector_service: ConnectorService):
        self._session = session
        self._connector_service = connector_service

    async def for_surface(
        self,
        surface: AgentSurfaceEntity,
        *,
        prefer_native: bool = False,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        # `from_address` is a property of the surface row (its provisioned
        # Resend address), never of the account's own credentials — inject it
        # unconditionally, regardless of which branch below resolves the rest.
        if prefer_native and has_native_credentials(surface.surface_type):
            credentials = native_credentials(surface.surface_type)
        elif surface.account_id is None:
            credentials = native_credentials(surface.surface_type)
        else:
            credentials = await self.for_account(
                surface.account_id, force_refresh=force_refresh
            )
        return self._with_resend_from_address(credentials, surface)

    @staticmethod
    def _with_resend_from_address(
        credentials: dict[str, Any], surface: AgentSurfaceEntity
    ) -> dict[str, Any]:
        """Inject the surface's provisioned address as the Resend ``from``."""
        if (
            surface.surface_type is SurfacePlatform.RESEND
            and surface.surface_identity_email
        ):
            return {**credentials, "from_address": surface.surface_identity_email}
        return credentials

    async def for_platform(
        self,
        platform: str | SurfacePlatform,
        account_id: UUID | str | None,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        if not account_id:
            return native_credentials(platform)
        return await self.for_account(account_id, force_refresh=force_refresh)

    async def for_account(
        self,
        account_id: UUID | str,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        account_model = await self._session.get(Account, UUID(str(account_id)))
        if account_model is None:
            return {}
        raw_stored = account_model.credentials or {}
        if not isinstance(raw_stored, dict) or raw_stored.get("_encrypted"):
            raw_stored = {}

        account = await self._connector_service.get_account(
            account_model.id,
            account_model.user_id,
        )
        stored = account.credentials or {}
        if hasattr(stored, "model_dump"):
            stored = stored.model_dump(exclude_none=True)
        for key, value in raw_stored.items():
            stored.setdefault(key, value)

        if account.connector_id in _SELF_MANAGED_CREDENTIAL_APPS:
            payload = dict(stored)
        else:
            try:
                refreshed = await self._connector_service.get_account_credentials(
                    account.id,
                    account.user_id,
                    force_refresh=force_refresh,
                )
                payload = (
                    refreshed.model_dump(exclude_none=True)
                    if hasattr(refreshed, "model_dump")
                    else {}
                )
            except Exception:
                logger.debug(
                    "agent_surfaces.credential_resolver.could_not_refresh_credentials_account.diagnostic",
                    account_id=account_id,
                )
                payload = dict(stored)

        for key in _CONTEXT_KEYS:
            if key not in payload and stored.get(key):
                payload[key] = stored[key]

        provider = await self._provider_for_account(account)
        if provider:
            # Platform adapters branch on this to choose Composio operations vs
            # native provider API calls (Composio never exposes a usable token).
            payload["provider"] = provider
        return payload

    async def slack_signing_secret(self, surface: Any) -> str | None:
        """The signing secret of the Slack app this surface's workspace runs on.

        Stored on the org's auth config beside the client id and secret, because
        all three belong to one Slack app — not to a surface. A surface is
        downstream of the app: you need the app to get a client id, the client
        id to connect the account, and the account before any surface exists.

        None means this workspace uses the deployment's Slack app.
        """
        account_id = getattr(surface, "account_id", None)
        if account_id is None:
            return None
        account = await self._connector_service.account_repository.get(account_id)
        auth_config_id = getattr(account, "auth_config_id", None)
        if auth_config_id is None:
            return None
        auth_config = await self._connector_service.auth_config_repository.get(
            auth_config_id
        )
        if auth_config is None:
            return None
        secret = (auth_config.config or {}).get("signing_secret")
        return str(secret).strip() or None if secret else None

    async def slack_webhook_credentials(
        self, surface: AgentSurfaceEntity
    ) -> SlackWebhookCredentials:
        account_id = surface.account_id
        if account_id is None:
            return SlackWebhookCredentials(
                app_id=None,
                signing_secret=surface_settings.slack_signing_secret,
                uses_custom_app=False,
            )
        account = await self._connector_service.account_repository.get(account_id)
        if account is None:
            return SlackWebhookCredentials(
                app_id=None,
                signing_secret=None,
                uses_custom_app=False,
            )
        app_id = self._slack_app_id(account.credentials)
        auth_config = await self._slack_auth_config(account.auth_config_id)
        uses_custom_app = self._is_org_custom_auth_config(auth_config)
        signing_secret = self._slack_secret_for_auth_config(
            auth_config, uses_custom_app=uses_custom_app
        )
        if app_id is None and not uses_custom_app:
            # An account on this deployment's own app that never stored an app id
            # — connected before we recorded it, or through a broker that drops
            # the field. We know which app it is: ours. Guessing is only safe
            # here; a custom app's id is the org's and cannot be inferred, so it
            # stays None and the surface is skipped as a verification candidate
            # rather than answering to our app's events.
            app_id = surface_settings.slack_app_id
        return SlackWebhookCredentials(
            app_id=app_id,
            signing_secret=signing_secret,
            uses_custom_app=uses_custom_app,
        )

    @staticmethod
    def _slack_app_id(credentials: Any) -> str | None:
        stored = credentials or {}
        if hasattr(stored, "model_dump"):
            stored = stored.model_dump(exclude_none=True)
        raw_response = (
            stored.get("raw_response") if isinstance(stored, dict) else None
        ) or {}
        return (
            str(
                raw_response.get("app_id") or raw_response.get("api_app_id") or ""
            ).strip()
            or None
        )

    async def _slack_auth_config(self, auth_config_id):
        if auth_config_id is None:
            return None
        return await self._connector_service.auth_config_repository.get(auth_config_id)

    @staticmethod
    def _is_org_custom_auth_config(auth_config) -> bool:
        source = getattr(auth_config, "config_source", None)
        source_value = str(getattr(source, "value", source) or "").upper()
        return source_value == AuthConfigSource.ORG_CUSTOM.value

    @staticmethod
    def _slack_secret_for_auth_config(
        auth_config, *, uses_custom_app: bool
    ) -> str | None:
        if uses_custom_app:
            configured = (getattr(auth_config, "config", None) or {}).get(
                "signing_secret"
            )
            return str(configured).strip() or None if configured else None
        return surface_settings.slack_signing_secret

    async def _provider_for_account(self, account: Any) -> str | None:
        auth_config_id = getattr(account, "auth_config_id", None)
        if auth_config_id is None:
            return None
        try:
            auth_config = await self._connector_service.auth_config_repository.get(
                auth_config_id
            )
        except Exception:
            logger.debug(
                "agent_surfaces.credential_resolver.could_not_resolve_provider_account.diagnostic"
            )
            return None
        if auth_config is None:
            return None
        kind = auth_config.kind
        return str(getattr(kind, "value", kind) or "").lower() or None
