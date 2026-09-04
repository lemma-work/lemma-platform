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

from app.modules.agent_surfaces.config import (
    resolve_resend_api_key,
    surface_settings,
)
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.surface_connectors import (
    SELF_MANAGED_CREDENTIAL_CONNECTOR_IDS,
)
from app.modules.connectors.contracts import AuthConfigSource
from app.modules.connectors.contracts.surfaces import (
    SurfaceAccount,
    account,
    account_with_secrets,
    app_signing_secret,
    auth_config,
    refreshed_credentials,
)

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


def with_surface_identity(
    credentials: dict[str, Any], surface: "AgentSurfaceEntity | None"
) -> dict[str, Any]:
    """Add the credential parts that belong to the surface row, not the platform.

    Resend's ``from_address`` is the address *that surface* was provisioned
    with, so no platform-level lookup can know it. It used to be applied in one
    place only, which made every other way of obtaining credentials quietly
    wrong: the send then failed with "Resend send requires api_key, from_address
    and a recipient", which reads like missing configuration and is really a
    missing join. Applying it here, on the way out of every path, is what stops
    that being re-discovered a fourth time.

    A no-op without a surface, so callers that genuinely have none (a
    system-credential conversation with no surface row) can call it freely.
    """
    if (
        surface is not None
        and surface.surface_type is SurfacePlatform.RESEND
        and surface.surface_identity_email
    ):
        return {**credentials, "from_address": surface.surface_identity_email}
    return credentials


def native_credentials(
    platform: str | SurfacePlatform | None,
    *,
    surface: "AgentSurfaceEntity | None" = None,
) -> dict[str, Any]:
    """System credentials from environment settings (WhatsApp/Telegram/Resend).

    Pass ``surface`` whenever one is in hand: the surface-derived parts are
    applied on the way out, so the fast path stays correct without a database
    round trip.
    """
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
        return with_surface_identity(credentials, surface)
    if normalized == SurfacePlatform.TELEGRAM:
        return with_surface_identity(
            {"bot_token": surface_settings.telegram_bot_token or ""}, surface
        )
    if normalized == SurfacePlatform.RESEND:
        return with_surface_identity(
            {
                "api_key": resolve_resend_api_key() or "",
                "from_name": surface_settings.resend_from_name or "Lemma",
            },
            surface,
        )
    return with_surface_identity({}, surface)


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
        # Both, because an address on an unowned domain is as unusable as no
        # key: the UI offered SYSTEM mode on the key alone and provisioning then
        # raised for the missing domain.
        return bool(resolve_resend_api_key() and surface_settings.resend_inbound_domain)
    return False


class SurfaceCredentialResolver:
    def __init__(self, *, uow):
        self._uow = uow

    async def for_surface(
        self,
        surface: AgentSurfaceEntity,
        *,
        prefer_native: bool = False,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        # Every branch returns through ``with_surface_identity``; see there for
        # why that is a property of the function rather than of the caller.
        if prefer_native and has_native_credentials(surface.surface_type):
            return native_credentials(surface.surface_type, surface=surface)
        if surface.account_id is None:
            return native_credentials(surface.surface_type, surface=surface)
        credentials = await self.for_account(
            surface.account_id, force_refresh=force_refresh
        )
        return with_surface_identity(credentials, surface)

    async def for_platform(
        self,
        platform: str | SurfacePlatform,
        account_id: UUID | str | None,
        *,
        surface: AgentSurfaceEntity | None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Credentials when the caller may not have a surface row.

        ``surface`` is required and may be ``None`` — deliberately not
        defaulted. Some credential values live on the surface rather than the
        platform (Resend's sender address), and every time a caller reached for
        the platform-level lookup while holding a surface, the result was
        silently incomplete and the send failed on a missing field. Making the
        argument explicit turns "did you have one?" into something the call site
        has to answer rather than something a reader has to infer.
        """
        if not account_id:
            return native_credentials(platform, surface=surface)
        credentials = await self.for_account(account_id, force_refresh=force_refresh)
        return with_surface_identity(credentials, surface)

    async def for_account(
        self,
        account_id: UUID | str,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        found = await account_with_secrets(self._uow, UUID(str(account_id)))
        if found is None:
            return {}
        connected, stored = found

        if connected.connector_id in _SELF_MANAGED_CREDENTIAL_APPS:
            payload = dict(stored)
        else:
            try:
                payload = dict(
                    await refreshed_credentials(
                        self._uow, connected.id, force_refresh=force_refresh
                    )
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

        provider = await self._provider_for_account(connected)
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
        connected = await account(self._uow, account_id)
        if connected is None or connected.auth_config_id is None:
            return None
        return await app_signing_secret(self._uow, connected.auth_config_id)

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
        found = await account_with_secrets(self._uow, account_id)
        if found is None:
            return SlackWebhookCredentials(
                app_id=None,
                signing_secret=None,
                uses_custom_app=False,
            )
        connected, credentials = found
        app_id = self._slack_app_id(credentials)
        install = await self._slack_auth_config(connected.auth_config_id)
        uses_custom_app = self._is_org_custom_install(install)
        signing_secret = await self._slack_secret_for_install(
            install, uses_custom_app=uses_custom_app
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
    def _slack_app_id(credentials: dict[str, Any]) -> str | None:
        raw_response = credentials.get("raw_response") or {}
        if not isinstance(raw_response, dict):
            return None
        return (
            str(
                raw_response.get("app_id") or raw_response.get("api_app_id") or ""
            ).strip()
            or None
        )

    async def _slack_auth_config(self, auth_config_id: UUID | None):
        if auth_config_id is None:
            return None
        return await auth_config(self._uow, auth_config_id)

    @staticmethod
    def _is_org_custom_install(install) -> bool:
        source = getattr(install, "config_source", None)
        return str(source or "").upper() == AuthConfigSource.ORG_CUSTOM.value

    async def _slack_secret_for_install(
        self, install, *, uses_custom_app: bool
    ) -> str | None:
        if not uses_custom_app:
            return surface_settings.slack_signing_secret
        if install is None:
            return None
        return await app_signing_secret(self._uow, install.id)

    async def _provider_for_account(self, connected: SurfaceAccount) -> str | None:
        if connected.auth_config_id is None:
            return None
        try:
            install = await auth_config(self._uow, connected.auth_config_id)
        except Exception:
            logger.debug(
                "agent_surfaces.credential_resolver.could_not_resolve_provider_account.diagnostic"
            )
            return None
        if install is None:
            return None
        return install.kind.lower() or None
