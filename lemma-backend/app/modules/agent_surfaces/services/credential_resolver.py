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

What leaves here is the platform's credentials model (`domain.credentials`):
secrets stay `SecretStr` until the HTTP call that sends them.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.reply_targets import (
    WhatsAppReplyTarget,
    reply_target_of,
)

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.config import reveal_secret
from app.modules.agent_surfaces.config import (
    resolve_resend_api_key,
    surface_settings,
)
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.credentials import (
    CredentialPayload,
    ResendCredentials,
    SurfaceCredentials,
    TelegramCredentials,
    WhatsAppCredentials,
    as_secret,
    credentials_for,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.surface_connectors import (
    SELF_MANAGED_CREDENTIAL_CONNECTOR_IDS,
)
from app.modules.agent_surfaces.domain.whatsapp_numbers import (
    WhatsAppNumberEntity,
)
from app.modules.agent_surfaces.infrastructure.repositories.whatsapp_number_repository import (
    WhatsAppNumberRepository,
)
from app.modules.connectors.contracts import AuthConfigSource
from app.modules.agent_surfaces.services.surface_connector import (
    bound_account_id,
    bound_account_ids,
)
from app.modules.connectors.contracts.pod_connectors import (
    resolve_standing_pod_connector,
)
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
    credentials: SurfaceCredentials, surface: "AgentSurfaceEntity | None"
) -> SurfaceCredentials:
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
        and isinstance(credentials, ResendCredentials)
        and surface.surface_identity_email
    ):
        return credentials.sending_as(surface.surface_identity_email)
    return credentials


def native_credentials(
    platform: str | SurfacePlatform,
    *,
    surface: "AgentSurfaceEntity | None" = None,
) -> SurfaceCredentials:
    """System credentials from environment settings (WhatsApp/Telegram/Resend).

    Pass ``surface`` whenever one is in hand: the surface-derived parts are
    applied on the way out, so the fast path stays correct without a database
    round trip. The settings' secrets go into the model as secrets; nothing here
    reveals them.
    """
    normalized = SurfacePlatform(str(platform).upper())
    credentials: SurfaceCredentials
    if normalized is SurfacePlatform.WHATSAPP:
        credentials = WhatsAppCredentials(
            access_token=as_secret(surface_settings.whatsapp_access_token),
            phone_number_id=surface_settings.whatsapp_phone_number_id or None,
            waba_id=surface_settings.whatsapp_waba_id or None,
            app_secret=as_secret(surface_settings.whatsapp_app_secret),
        )
    elif normalized is SurfacePlatform.TELEGRAM:
        credentials = TelegramCredentials(
            bot_token=as_secret(surface_settings.telegram_bot_token)
        )
    elif normalized is SurfacePlatform.RESEND:
        credentials = ResendCredentials(
            api_key=as_secret(resolve_resend_api_key()),
            from_name=surface_settings.resend_from_name or "Lemma",
        )
    else:
        credentials = credentials_for(normalized, {})
    return with_surface_identity(credentials, surface)


def has_native_credentials(platform: str | SurfacePlatform | None) -> bool:
    normalized = str(platform or "").upper()
    if normalized == SurfacePlatform.WHATSAPP:
        return bool(
            reveal_secret(surface_settings.whatsapp_access_token)
            and surface_settings.whatsapp_phone_number_id
        )
    if normalized == SurfacePlatform.TELEGRAM:
        return bool(reveal_secret(surface_settings.telegram_bot_token))
    if normalized == SurfacePlatform.RESEND:
        # Both, because an address on an unowned domain is as unusable as no
        # key: the UI offered SYSTEM mode on the key alone and provisioning then
        # raised for the missing domain.
        return bool(resolve_resend_api_key() and surface_settings.resend_inbound_domain)
    return False


def arrival_number(event: ParsedInboundSurfaceEvent | None) -> str | None:
    """The WhatsApp number an inbound message arrived on, if it names one.

    The one place this is read off an event, so every step that acts on an
    inbound message -- the read receipt, the typing indicator, the media
    download, the fallback reply, the reply itself -- asks the resolver about
    the same number. Each used to decide separately, and the ones that did not
    ask used the settings token for a message a pooled number received.
    """
    if event is None:
        return None
    target = reply_target_of(event)
    return target.phone_number_id if isinstance(target, WhatsAppReplyTarget) else None


class PooledNumberReader(Protocol):
    """Reads one pool row by its Graph phone number id."""

    async def get_by_phone_number_id(
        self, phone_number_id: str
    ) -> WhatsAppNumberEntity | None: ...


class SurfaceCredentialResolver:
    def __init__(self, *, uow, pooled_numbers: PooledNumberReader | None = None):
        self._uow = uow
        # A collaborator with a default built from the `uow`, rather than a
        # repository this constructs mid-method: which number a reply goes out
        # from is a decision worth testing without Postgres, and the alternative
        # is a test replacing the repository class inside this module -- which
        # keeps passing after this stops calling it.
        self._pooled_numbers = pooled_numbers

    def _numbers(self) -> PooledNumberReader:
        return self._pooled_numbers or WhatsAppNumberRepository(self._uow)

    async def _pooled_overrides(
        self,
        surface: AgentSurfaceEntity | None,
        arrived_on: str | None = None,
    ) -> dict[str, str]:
        """What the number this surface holds answers with, if it holds one.

        Returns only overrides, laid over the settings answer by
        `_with_pooled_number`, so a number that leaves a field out keeps the
        deployment's value for it.

        `native_credentials` is a pure function over settings and stays that
        way: it is called from synchronous code and from paths with no unit of
        work, and giving it a database read would make every one of those a lie.
        The pool read belongs here instead, where there is a `uow` and where the
        result is resolved once per delivery.

        Absent overrides leave the settings answer standing, which is what makes
        a single-number deployment need no rows.

        ``arrived_on`` is the number the message being answered came in on, and
        it is what makes a pooled number work for a surface that holds none.
        `_ensure_shared_surface` mints the shared-line surface every chat signup
        lands on, and deliberately leaves `surface_identity_id` NULL -- several
        personal pods in one organization ride one line, and stamping the number
        would put them under `uq_agent_org_whatsapp_number` and refuse the
        second person to sign up. So the surface cannot say which number to
        answer from, and without this the settings number answered every one of
        them: somebody who wrote to a pooled number got a reply from a different
        number, and if that number sits under another WABA the token is not even
        authorised to send as it.

        The surface still wins where it has an answer. A deliberately allocated
        number is a property of the surface and holds for messages it starts,
        which have no inbound event to have arrived on at all.
        """
        phone_number_id = (
            surface.surface_identity_id if surface is not None else None
        ) or arrived_on
        if not phone_number_id:
            return {}
        number = await self._numbers().get_by_phone_number_id(phone_number_id)
        if number is None:
            if surface is not None and surface.surface_identity_id:
                # The row was removed while a surface still named it. The
                # settings number is the wrong answer but it is a working one,
                # and refusing here would take the surface off the air over an
                # inventory edit.
                logger.warning(
                    "agent_surfaces.credential_resolver.pooled_number_missing.degraded",
                    surface_id=str(surface.id),
                    phone_number_id=phone_number_id,
                )
            # Nothing logged when the number came from the inbound message
            # instead: a deployment running the one number from settings has no
            # pool rows at all, so every reply it ever sends misses here. That
            # is the single-number case working exactly as it did before the
            # pool existed, not a degradation, and warning on it would put a
            # line in the log for every message the deployment answers.
            return {}
        return number.credential_overrides()

    async def _with_pooled_number(
        self,
        credentials: SurfaceCredentials,
        surface: AgentSurfaceEntity | None,
        arrived_on: str | None,
    ) -> SurfaceCredentials:
        """System credentials, answering from the pooled number where one holds.

        WhatsApp only: no other platform has a pool. The platform stands in for
        the surface when there is none -- the worker acting on an inbound
        message holds only the run's context, and the number it arrived on is
        still the one whose token may mark it read.
        """
        if not isinstance(credentials, WhatsAppCredentials):
            return credentials
        return credentials.overlaid_with(
            await self._pooled_overrides(surface, arrived_on)
        )

    async def for_surface(
        self,
        surface: AgentSurfaceEntity,
        *,
        prefer_native: bool = False,
        force_refresh: bool = False,
        arrived_on: str | None = None,
    ) -> SurfaceCredentials:
        # Every branch returns through ``with_surface_identity``; see there for
        # why that is a property of the function rather than of the caller.
        if surface.pod_connector_slug is None or (
            prefer_native and has_native_credentials(surface.surface_type)
        ):
            return await self._with_pooled_number(
                native_credentials(surface.surface_type, surface=surface),
                surface,
                arrived_on,
            )
        credentials = await self.for_account(
            await self.account_id_for(surface),
            platform=surface.surface_type,
            force_refresh=force_refresh,
        )
        return with_surface_identity(credentials, surface)

    async def account_id_for(self, surface: AgentSurfaceEntity) -> UUID | None:
        """The account this surface runs as right now, or ``None`` on system
        credentials.

        Resolved through its pod connector on every call, never stored: an
        owner withdrawing the account, the connector being removed, the
        installation disabled or the account needing reauth each raise the
        connector's typed error here, so the surface stops at once and says
        why instead of running on a credential nobody offers any more.
        """
        return await bound_account_id(self._uow, surface)

    async def for_connector(
        self,
        platform: str | SurfacePlatform,
        *,
        pod_id: UUID,
        slug: str,
        force_refresh: bool = False,
    ) -> SurfaceCredentials:
        """The credentials of the account a pod connector runs as now.

        For a step that carries the surface's connector rather than the
        surface row (``SurfaceConnectorRef``).
        """
        resolved = await resolve_standing_pod_connector(
            self._uow, pod_id=pod_id, slug=slug
        )
        return await self.for_account(
            resolved.account_id, platform=platform, force_refresh=force_refresh
        )

    async def _verifying_account(self, surface: AgentSurfaceEntity) -> UUID | None:
        """The account a delivery to this surface is verified against, read
        with no side effect: this runs for every candidate of every webhook, in
        the request. A connector that runs as nothing right now verifies
        nothing -- the surface is skipped as a candidate, exactly as one whose
        account row is gone."""
        return (await bound_account_ids(self._uow, [surface])).get(surface.id)

    async def installation_identity(self, surface: AgentSurfaceEntity) -> UUID:
        """What a verified binding through this surface is keyed on: the bot's
        own account where it runs as one, the surface itself on the
        deployment's shared credentials."""
        return await self.account_id_for(surface) or surface.id

    async def for_platform(
        self,
        platform: str | SurfacePlatform,
        *,
        surface: AgentSurfaceEntity | None,
        arrived_on: str | None = None,
    ) -> SurfaceCredentials:
        """The deployment's own credentials, for a step with no surface row.

        A surface that runs as a pod connector is never resolved here: its
        account comes from the connector, through ``for_surface``.

        ``surface`` is required and may be ``None`` — deliberately not
        defaulted. Some credential values live on the surface rather than the
        platform (Resend's sender address), and every time a caller reached for
        the platform-level lookup while holding a surface, the result was
        silently incomplete and the send failed on a missing field. Making the
        argument explicit turns "did you have one?" into something the call site
        has to answer rather than something a reader has to infer.

        ``arrived_on`` is :func:`arrival_number` of the message being acted on,
        and it matters here more than anywhere: this is what the worker calls,
        and without it a message that came in on a pooled number was marked
        read, shown a typing indicator and had its media fetched with the
        settings token -- which belongs to another number and, under another
        WABA, is refused outright.
        """
        return await self._with_pooled_number(
            native_credentials(platform, surface=surface), surface, arrived_on
        )

    async def for_account(
        self,
        account_id: UUID | str | None,
        *,
        platform: str | SurfacePlatform,
        force_refresh: bool = False,
    ) -> SurfaceCredentials:
        """The account's credentials, read as the platform's model."""
        return credentials_for(
            platform,
            await self._account_payload(account_id, force_refresh=force_refresh),
        )

    async def _account_payload(
        self,
        account_id: UUID | str | None,
        *,
        force_refresh: bool = False,
    ) -> CredentialPayload:
        if account_id is None:
            return {}
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
                # The stored token is used instead, and if it has expired every
                # call it makes is refused -- which reads as a broken surface
                # unless this line says why.
                logger.warning(
                    "agent_surfaces.credential_resolver.credential_refresh_failed.degraded",
                    account_id=str(account_id),
                    exc_info=True,
                )
                payload = dict(stored)

        for key in _CONTEXT_KEYS:
            if key not in payload and stored.get(key):
                payload[key] = stored[key]

        provider = await self._provider_for_account(connected)
        if provider:
            # Which kind of auth config the account was connected through.
            payload["provider"] = provider
        return payload

    async def slack_signing_secret(self, surface: AgentSurfaceEntity) -> str | None:
        """The signing secret of the Slack app this surface's workspace runs on.

        Stored on the org's auth config beside the client id and secret, because
        all three belong to one Slack app — not to a surface. A surface is
        downstream of the app: you need the app to get a client id, the client
        id to connect the account, and the account before any surface exists.

        None means this workspace uses the deployment's Slack app.
        """
        account_id = await self._verifying_account(surface)
        if account_id is None:
            return None
        connected = await account(self._uow, account_id)
        if connected is None or connected.auth_config_id is None:
            return None
        return await app_signing_secret(self._uow, connected.auth_config_id)

    async def slack_webhook_credentials(
        self, surface: AgentSurfaceEntity
    ) -> SlackWebhookCredentials:
        if surface.pod_connector_slug is None:
            return SlackWebhookCredentials(
                app_id=None,
                signing_secret=reveal_secret(surface_settings.slack_signing_secret),
                uses_custom_app=False,
            )
        account_id = await self._verifying_account(surface)
        found = (
            await account_with_secrets(self._uow, account_id)
            if account_id is not None
            else None
        )
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
    def _slack_app_id(credentials: Mapping[str, object]) -> str | None:
        raw_response = credentials.get("raw_response") or {}
        if not isinstance(raw_response, Mapping):
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
            return reveal_secret(surface_settings.slack_signing_secret)
        if install is None:
            return None
        return await app_signing_secret(self._uow, install.id)

    async def _provider_for_account(self, connected: SurfaceAccount) -> str | None:
        if connected.auth_config_id is None:
            return None
        try:
            install = await auth_config(self._uow, connected.auth_config_id)
        except Exception:
            logger.warning(
                "agent_surfaces.credential_resolver.provider_lookup_failed.degraded",
                auth_config_id=str(connected.auth_config_id),
                exc_info=True,
            )
            return None
        if install is None:
            return None
        return install.kind.lower() or None
