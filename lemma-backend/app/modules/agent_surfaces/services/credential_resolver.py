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

from typing import Any
from uuid import UUID

from app.modules.agent_surfaces.config import surface_settings
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.services.connector_service import ConnectorService

logger = get_logger(__name__)

# Connectors that manage service-level credentials (no OAuth refresh flow).
# Resend uses a static API key, not a 3-legged OAuth token — routing it through
# the OAuth refresh flow would silently drop `api_key` (ConnectorService's
# `_to_oauth_credentials` only carries access_token/refresh_token/token_type/
# expires_at/raw_response/connection_id; `api_key` isn't one of them, and it
# isn't in `_CONTEXT_KEYS` below either, so nothing rescues it afterward).
_SELF_MANAGED_CREDENTIAL_APPS = frozenset({"teams", "whatsapp", "telegram", "resend"})

# Non-secret keys platform adapters read for identity/routing context.
_CONTEXT_KEYS = ("scope", "scopes", "api_base_url", "raw_response", "user_data")


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
        return bool(surface_settings.whatsapp_access_token and surface_settings.whatsapp_phone_number_id)
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
            except Exception as exc:
                logger.warning(
                    "Could not refresh credentials for account %s, falling back to stored: %s",
                    account_id,
                    exc,
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

    async def _provider_for_account(self, account: Any) -> str | None:
        auth_config_id = getattr(account, "auth_config_id", None)
        if auth_config_id is None:
            return None
        try:
            auth_config = await self._connector_service.auth_config_repository.get(
                auth_config_id
            )
        except Exception as exc:
            logger.warning(
                "Could not resolve provider for account %s: %s", account.id, exc
            )
            return None
        if auth_config is None:
            return None
        provider = auth_config.provider
        return str(getattr(provider, "value", provider) or "").upper() or None
