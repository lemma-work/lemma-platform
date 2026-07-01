from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any
from uuid import UUID
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.platforms.common import (
    computed_webhook_url,
    public_https_api_url_available,
)
from app.modules.agent_surfaces.platforms.delivery import RetryPolicy, with_retry
from app.modules.agent_surfaces.platforms.telegram.client import (
    ALLOWED_UPDATES,
    TelegramApiError,
    TelegramClient,
    classify_telegram_error,
    telegram_retry_after,
)
from app.modules.agent_surfaces.platforms.telegram.mode import (
    telegram_requires_webhook_setup,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    AgentSurfaceStatus,
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfaceEventMode,
    SurfaceMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceAlreadyExistsError,
    AgentSurfacePlatformError,
    AgentSurfaceNotFoundError,
    AgentSurfaceValidationError,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceAccountBindingPort,
    SurfaceAccountInfo,
    SurfaceAccountPort,
    SurfaceAuthConfigPort,
    SurfaceInstallationRepositoryPort,
)
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.agent_surfaces.domain.setup_guides import (
    SurfacePlatformSetupGuide,
    build_surface_setup_actions,
    build_surface_setup_guide,
)
from app.modules.connectors.infrastructure.repositories.connector_trigger_repository import (
    ConnectorTriggerRepository,
)
from app.modules.schedule.domain.schedule import (
    ScheduleCreateEntity,
    ScheduleType,
    ScheduleUpdateEntity,
)
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.services.event_receiver_service import (
    notify_surface_receiver_config_changed,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from app.core.authorization.context import Context
    from app.modules.agent_surfaces.domain.models import SurfaceChannelInfo
    from app.modules.agent_surfaces.services.credential_resolver import (
        SurfaceCredentialResolver,
    )
    from app.modules.schedule.services.schedule_service import ScheduleService

_GRAPH_SCOPE = "https://graph.microsoft.com/.default"

# Shared Redis cache of Teams admin-consent probe results (per-entry TTL: 60 s
# granted / 10 s denied), so the Graph probe is shared across replicas. Redis
# unavailable -> re-probe (never fails).
_consent_check_cache: RedisJsonCache | None = None


def _get_consent_cache() -> RedisJsonCache:
    global _consent_check_cache
    if (
        _consent_check_cache is None
        or _consent_check_cache._redis_url != settings.redis_url
    ):
        _consent_check_cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="surface:teams-consent",
            ttl_seconds=60,
        )
    return _consent_check_cache
_EMAIL_TRIGGER_EVENT_TYPES: dict[str, tuple[str, ...]] = {
    "GMAIL": "GMAIL_NEW_GMAIL_MESSAGE",
    "OUTLOOK": "OUTLOOK_MESSAGE_TRIGGER",
}
# Bounded retry for the in-process Telegram webhook registration calls.
_WEBHOOK_RETRY_POLICY = RetryPolicy(max_attempts=3, base_delay=0.5)


class AgentSurfaceService:
    def __init__(
        self,
        *,
        surface_repository: SurfaceInstallationRepositoryPort,
        account_binding_resolver: SurfaceAccountBindingPort,
        schedule_service: "ScheduleService | None" = None,
        connector_trigger_repository: ConnectorTriggerRepository | None = None,
        account_port: SurfaceAccountPort | None = None,
        auth_config_port: SurfaceAuthConfigPort | None = None,
        credential_resolver: "SurfaceCredentialResolver | None" = None,
        adapter_registry: "SurfacePlatformAdapterRegistry | None" = None,
    ):
        self.surface_repository = surface_repository
        self.account_binding_resolver = account_binding_resolver
        self.schedule_service = schedule_service
        self.connector_trigger_repository = connector_trigger_repository
        self._account_port = account_port
        self._auth_config_port = auth_config_port
        self._credential_resolver = credential_resolver
        self._adapter_registry = adapter_registry or SurfacePlatformAdapterRegistry()

    async def list_channels(
        self, *, surface: AgentSurfaceEntity
    ) -> list["SurfaceChannelInfo"]:
        """List the channels/groups the surface bot can be configured in.

        Empty for platforms without enumerable channels, or when credentials
        cannot be resolved.
        """
        adapter = self._adapter_registry.get(surface.surface_type)
        if adapter is None or self._credential_resolver is None:
            return []
        credentials = await self._credential_resolver.for_surface(surface)
        return await adapter.list_channels(credentials=credentials)

    async def create_surface(
        self,
        *,
        pod_id: UUID,
        agent_id: UUID | None,
        platform: SurfacePlatform,
        name: str | None = None,
        config: SurfaceConfig | None = None,
        mode: SurfaceMode | None = None,
        event_mode: SurfaceEventMode | None = None,
        credential_mode: SurfaceCredentialMode | None = None,
        account_id: UUID | None = None,
        external_workspace_id: str | None = None,
        external_tenant_id: str | None = None,
        external_channel_id: str | None = None,
        ctx: Context | None = None,
    ) -> AgentSurfaceEntity:
        # A surface is addressed by its pod-unique name (defaults to the
        # platform); several surfaces of the same platform can coexist under
        # distinct names (e.g. different bots → different agents). Distinct bot
        # accounts are still enforced by the credential/account conflict checks
        # below.
        resolved_name = (name or "").strip() or AgentSurfaceEntity.default_name_for(platform)
        existing = await self.surface_repository.get_by_pod_and_name(
            pod_id=pod_id, name=resolved_name
        )
        if isinstance(existing, AgentSurfaceEntity):
            raise AgentSurfaceAlreadyExistsError(resolved_name)
        (
            resolved_tenant_id,
            resolved_workspace_id,
            surface_identity_id,
        ) = await self.account_binding_resolver.resolve_binding(
            platform,
            account_id=account_id,
        )
        entity = AgentSurfaceEntity.create(
            pod_id=pod_id,
            surface_type=platform,
            name=resolved_name,
            agent_id=agent_id,
            config=config,
            mode=mode,
            event_mode=event_mode,
            credential_mode=credential_mode,
            account_id=account_id,
            external_workspace_id=external_workspace_id or resolved_workspace_id,
            external_tenant_id=external_tenant_id or resolved_tenant_id,
            external_channel_id=external_channel_id,
            surface_identity_id=surface_identity_id,
        )
        # Resend is a system-credentialed email surface: provision a unique
        # per-pod inbound/outbound address that inbound routing matches on and
        # outbound uses as the From. (Other email surfaces get this from their
        # connected account.)
        if (
            platform is SurfacePlatform.RESEND
            and not entity.surface_identity_email
        ):
            entity.surface_identity_email = self._provision_resend_address(pod_id)
        self._validate_runtime_supported(entity)
        await self._ensure_unique_org_credential_binding(entity)
        telegram_credentials: dict[str, Any] | None = None
        if telegram_requires_webhook_setup(entity):
            await self._ensure_unique_telegram_account(entity)
            telegram_credentials = await self._prepare_telegram_webhook(entity)
        created = await self.surface_repository.create(entity)
        if telegram_credentials is not None:
            await self._register_telegram_webhook(
                credentials=telegram_credentials,
                webhook_url=self._build_public_surface_webhook_url(created.id),
                webhook_secret=created.webhook_secret or "",
            )
        synced = await self._sync_email_schedule(created, previous_surface=None, ctx=ctx)
        await notify_surface_receiver_config_changed(synced.id)
        return synced

    @staticmethod
    def _provision_resend_address(pod_id: UUID) -> str:
        """Derive a unique per-pod inbound/outbound Resend address.

        Uses the pod id for uniqueness under a catch-all inbound domain
        (``*@<domain>`` → one webhook), so no per-address API registration.
        """
        domain = surface_settings.resend_inbound_domain or "ops.lemma.work"
        return f"pod-{pod_id.hex[:12]}@{domain}"

    async def get_surface(self, surface_id: UUID) -> AgentSurfaceEntity:
        surface = await self.surface_repository.get(surface_id)
        if surface is None:
            raise AgentSurfaceNotFoundError(str(surface_id))
        return surface

    async def get_surface_in_pod(
        self,
        *,
        pod_id: UUID,
        surface_id: UUID,
    ) -> AgentSurfaceEntity:
        surface = await self.get_surface(surface_id)
        if surface.pod_id != pod_id:
            raise AgentSurfaceNotFoundError(str(surface_id))
        return surface

    async def get_surface_by_name_in_pod(
        self,
        *,
        pod_id: UUID,
        name: str,
    ) -> AgentSurfaceEntity:
        surface = await self.surface_repository.get_by_pod_and_name(
            pod_id=pod_id, name=name
        )
        if surface is None:
            raise AgentSurfaceNotFoundError(name)
        return surface

    async def update_surface(
        self,
        *,
        surface_id: UUID,
        agent_id: UUID | None = None,
        update_agent_id: bool = False,
        config: SurfaceConfig | None = None,
        mode: SurfaceMode | None = None,
        event_mode: SurfaceEventMode | None = None,
        credential_mode: SurfaceCredentialMode | None = None,
        account_id: UUID | None = None,
        external_workspace_id: str | None = None,
        external_tenant_id: str | None = None,
        external_channel_id: str | None = None,
        is_active: bool | None = None,
        ctx: Context | None = None,
    ) -> AgentSurfaceEntity:
        surface = await self.get_surface(surface_id)
        previous_surface = surface.model_copy(deep=True)
        telegram_credentials: dict[str, Any] | None = None

        if update_agent_id:
            surface.update_agent(agent_id)

        if (
            config is not None
            or account_id is not None
            or mode is not None
            or event_mode is not None
            or credential_mode is not None
            or external_workspace_id is not None
            or external_tenant_id is not None
            or external_channel_id is not None
        ):
            (
                resolved_tenant_id,
                resolved_workspace_id,
                surface_identity_id,
            ) = await self.account_binding_resolver.resolve_binding(
                surface.surface_type,
                account_id=account_id if account_id is not None else surface.account_id,
            )
            surface.update_config(
                config if config is not None else surface.config,
                account_id=account_id,
                mode=mode,
                event_mode=event_mode,
                credential_mode=credential_mode,
                external_workspace_id=external_workspace_id or resolved_workspace_id,
                external_tenant_id=external_tenant_id or resolved_tenant_id,
                external_channel_id=external_channel_id,
                surface_identity_id=surface_identity_id,
            )
            self._validate_runtime_supported(surface)
            await self._ensure_unique_org_credential_binding(surface)
        if is_active is not None:
            surface.toggle_active(is_active)

        previous_telegram_webhook_enabled = (
            previous_surface.is_active
            and telegram_requires_webhook_setup(previous_surface)
        )
        current_telegram_webhook_enabled = (
            surface.is_active
            and telegram_requires_webhook_setup(surface)
        )
        telegram_binding_changed = (
            previous_surface.account_id != surface.account_id
            or previous_surface.event_mode != surface.event_mode
        )
        should_disable_telegram_webhook = previous_telegram_webhook_enabled and (
            not current_telegram_webhook_enabled or telegram_binding_changed
        )
        should_register_telegram_webhook = current_telegram_webhook_enabled and (
            not previous_telegram_webhook_enabled
            or telegram_binding_changed
            or not surface.webhook_secret
        )
        if should_register_telegram_webhook and telegram_credentials is None:
            await self._ensure_unique_telegram_account(surface)
            telegram_credentials = await self._prepare_telegram_webhook(surface)
        if should_disable_telegram_webhook:
            await self._delete_telegram_webhook(previous_surface)

        updated = await self.surface_repository.update(surface)
        if telegram_credentials is not None:
            await self._register_telegram_webhook(
                credentials=telegram_credentials,
                webhook_url=self._build_public_surface_webhook_url(updated.id),
                webhook_secret=updated.webhook_secret or "",
            )
        synced = await self._sync_email_schedule(
            updated,
            previous_surface=previous_surface,
            ctx=ctx,
        )
        await notify_surface_receiver_config_changed(synced.id)
        return synced

    async def list_surfaces_by_pod(
        self,
        pod_id: UUID,
        *,
        platform: str | None = None,
        agent_id: UUID | None = None,
        match_agent: bool = False,
        cursor: UUID | None = None,
        limit: int = 100,
    ) -> tuple[list[AgentSurfaceEntity], UUID | None]:
        return await self.surface_repository.list_by_pod(
            pod_id,
            platform=platform,
            agent_id=agent_id,
            match_agent=match_agent,
            cursor=cursor,
            limit=limit,
        )

    async def delete_surface(self, surface_id: UUID) -> None:
        surface = await self.surface_repository.get(surface_id)
        if surface is not None:
            if telegram_requires_webhook_setup(surface):
                await self._delete_telegram_webhook(surface)
            await self._delete_email_schedule_if_needed(surface)
        await self.surface_repository.delete(surface_id)
        await notify_surface_receiver_config_changed(surface_id)

    async def delete_all_surfaces_for_pod(self, pod_id: UUID) -> int:
        """Remove every surface in a pod so its accounts become free again.

        Best-effort per surface: a failed external teardown is logged and
        skipped. ``delete_surface`` deletes the row regardless, so the
        org-unique account binding is always released.
        """
        deleted = 0
        cursor: UUID | None = None
        while True:
            surfaces, cursor = await self.list_surfaces_by_pod(pod_id, cursor=cursor)
            for surface in surfaces:
                try:
                    await self.delete_surface(surface.id)
                    deleted += 1
                except Exception:
                    logger.exception(
                        "Failed to delete surface %s during pod %s cleanup",
                        surface.id,
                        pod_id,
                    )
            if cursor is None:
                break
        return deleted

    def get_platform_setup_guide(self, platform: str) -> SurfacePlatformSetupGuide:
        resolved_platform = SurfacePlatform.from_source(platform)
        if resolved_platform is None:
            normalized = str(platform).upper()
            try:
                resolved_platform = SurfacePlatform[normalized]
            except KeyError as exc:
                raise AgentSurfaceValidationError(
                    f"Unsupported surface platform '{platform}'"
                ) from exc
        return build_surface_setup_guide(resolved_platform)

    async def get_surface_setup_by_name(
        self,
        *,
        pod_id: UUID,
        name: str,
    ) -> dict[str, Any]:
        """Everything needed to finish setting up an existing surface, in one read.

        Merges the static platform checklist with the live webhook and
        admin-consent state. Raises ``AgentSurfaceNotFoundError`` if no surface
        has this name — use ``get_platform_setup_guide`` for the pre-creation
        checklist, which needs no surface to exist yet.
        """
        surface = await self.get_surface_by_name_in_pod(pod_id=pod_id, name=name)
        guide = self.get_platform_setup_guide(surface.surface_type.value)
        webhook_url = computed_webhook_url(surface)
        admin_consent = await self._surface_admin_consent(surface)
        actions = build_surface_setup_actions(
            platform=surface.surface_type,
            is_custom_app=await self._surface_uses_org_custom_app(surface),
            webhook_url=webhook_url,
            slack_socket_mode=surface_settings.enable_slack_socket_mode,
            whatsapp_verify_token=surface_settings.whatsapp_verify_token,
        )
        pending_consent = bool(
            admin_consent and admin_consent["required"] and not admin_consent["granted"]
        )
        return {
            "platform": surface.surface_type,
            "exists": True,
            "status": surface.status,
            "ready": not actions and not pending_consent,
            "webhook_url": webhook_url,
            "admin_consent": admin_consent,
            "actions": actions,
            "guide": guide,
        }

    async def _surface_uses_org_custom_app(
        self, surface: AgentSurfaceEntity
    ) -> bool:
        """True when the surface's account was set up with the org's own OAuth
        app (auth config ``ORG_CUSTOM``), so the org must point that app's
        webhook at Lemma. System/Lemma-managed auth configs need no setup."""
        from app.modules.connectors.domain.auth_config import AuthConfigSource

        if (
            surface.account_id is None
            or self._account_port is None
            or self._auth_config_port is None
        ):
            return False
        account = await self._account_port.get_account(surface.account_id)
        if account is None or account.auth_config_id is None:
            return False
        auth_config = await self._auth_config_port.get_auth_config(
            account.auth_config_id
        )
        return bool(
            auth_config
            and auth_config.config_source == AuthConfigSource.ORG_CUSTOM.value
        )

    async def _surface_admin_consent(
        self, surface: AgentSurfaceEntity
    ) -> dict[str, Any] | None:
        """Teams admin-consent state, or None for platforms that never need it."""
        if surface.surface_type is not SurfacePlatform.TEAMS:
            return None
        info = await self.get_admin_consent_info(surface)
        return {
            "required": True,
            "granted": info.get("status") is AgentSurfaceStatus.ACTIVE,
            "consent_url": info.get("consent_url"),
        }

    async def activate_after_consent(
        self,
        *,
        surface_id: UUID,
        tenant_id: str,
    ) -> AgentSurfaceEntity | None:
        surface = await self.surface_repository.get(surface_id)
        if surface is None:
            return None

        if not surface.external_tenant_id:
            surface.external_tenant_id = tenant_id

        surface.activate()
        return await self.surface_repository.update(surface)

    async def get_admin_consent_info(
        self, surface: AgentSurfaceEntity
    ) -> dict[str, Any]:
        if surface.surface_type != SurfacePlatform.TEAMS:
            return {"status": surface.status}

        tenant_id = surface.external_tenant_id
        if not tenant_id:
            return {"status": surface.status, "consent_url": None}

        if surface.status is AgentSurfaceStatus.ACTIVE:
            return {"status": AgentSurfaceStatus.ACTIVE}

        already_granted = await self._check_admin_consent_granted(tenant_id)
        if already_granted:
            surface.activate()
            await self.surface_repository.update(surface)
            return {"status": AgentSurfaceStatus.ACTIVE}

        consent_url = self._build_consent_url(surface.id, tenant_id)
        return {
            "status": AgentSurfaceStatus.PENDING_ADMIN_CONSENT,
            "consent_url": consent_url,
        }

    def _build_consent_url(self, surface_id: UUID, tenant_id: str) -> str:
        callback_base = settings.api_url.rstrip("/")
        params = urlencode({
            "client_id": surface_settings.microsoft_bot_app_id or "",
            "redirect_uri": f"{callback_base}/surfaces/teams/admin-consent/callback",
            "state": str(surface_id),
        })
        return f"https://login.microsoftonline.com/{tenant_id}/adminconsent?{params}"

    async def _check_admin_consent_granted(self, tenant_id: str) -> bool:
        app_id = surface_settings.microsoft_bot_app_id
        app_password = surface_settings.microsoft_bot_app_password
        if not app_id or not app_password:
            return False

        cache_key = f"consent_check:{tenant_id}"
        cache = _get_consent_cache()
        try:
            cached = await cache.get_json(cache_key)
        except Exception:
            cached = None
        if cached is not None:
            return bool(cached)

        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                token_response = await client.post(token_url, data={
                    "grant_type": "client_credentials",
                    "client_id": app_id,
                    "client_secret": app_password,
                    "scope": _GRAPH_SCOPE,
                })
                if token_response.status_code != 200:
                    try:
                        await cache.set_json(cache_key, False, ttl_seconds=10)
                    except Exception:
                        pass
                    return False
                token = token_response.json().get("access_token")
        except Exception:
            return False

        if not token:
            return False

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                probe = await client.get(
                    "https://graph.microsoft.com/v1.0/users?$top=1&$select=id",
                    headers={"Authorization": f"Bearer {token}"},
                )
                granted = probe.status_code == 200
        except Exception:
            granted = False

        try:
            await cache.set_json(cache_key, granted, ttl_seconds=60 if granted else 10)
        except Exception:
            pass
        return granted

    async def _sync_email_schedule(
        self,
        surface: AgentSurfaceEntity,
        *,
        previous_surface: AgentSurfaceEntity | None,
        ctx: Context | None = None,
    ) -> AgentSurfaceEntity:
        # Only Composio-trigger email surfaces (Gmail/Outlook) get a polling
        # schedule. Resend is an email surface but receives over a native webhook,
        # so it has no schedule.
        if (
            not self._is_email_surface(surface)
            or surface.event_mode is not SurfaceEventMode.COMPOSIO_TRIGGER
        ):
            if previous_surface is not None:
                await self._delete_email_schedule_if_needed(previous_surface)
            return surface

        if self.schedule_service is None or self.connector_trigger_repository is None:
            raise AgentSurfaceValidationError(
                "Email surfaces require schedule service dependencies"
            )

        if surface.account_id is None:
            raise AgentSurfaceValidationError("Email surfaces require account_id")
        account = await self._get_connected_account(surface.account_id)
        if surface.surface_type is SurfacePlatform.GMAIL and not account.email:
            # Gmail polling filters out the surface's own messages by email
            # (query below); Outlook routes by account_id and works without it.
            raise AgentSurfaceValidationError(
                "Connected account must expose an email address for Gmail surfaces"
            )
        await self._ensure_composio_email_account(account)
        connector_trigger_id = await self._resolve_email_connector_trigger_id(
            surface.surface_type
        )

        existing_schedule_id = surface.schedule_id
        previous_schedule_id = None
        if previous_surface is not None and self._is_email_surface(previous_surface):
            previous_schedule_id = previous_surface.schedule_id

        # Recreate the schedule when the connected account changes.
        previous_account_id = None
        if previous_surface is not None and self._is_email_surface(previous_surface):
            previous_account_id = previous_surface.account_id
        if previous_schedule_id and previous_account_id != surface.account_id:
            await self.schedule_service.delete_schedule(previous_schedule_id)
            existing_schedule_id = None

        if existing_schedule_id is None:
            schedule_config: dict[str, Any] = {
                "source": "agent_surfaces_email",
                "surface_id": str(surface.id),
                "platform": surface.surface_type.value.lower(),
            }
            if surface.surface_type is SurfacePlatform.GMAIL:
                schedule_config.update({
                    "userId": "me",
                    "interval": 2,
                    "labelIds": "INBOX",
                    "query": f"label:inbox -from:{account.email}",
                })
            created_schedule = await self.schedule_service.create_schedule(
                ScheduleCreateEntity(
                    user_id=account.user_id,
                    pod_id=surface.pod_id,
                    name=(
                        f"agent_surface_{surface.surface_type.value.lower()}_"
                        f"{str(surface.id).replace('-', '')[:8]}"
                    ),
                    schedule_type=ScheduleType.WEBHOOK,
                    account_id=account.id,
                    connector_trigger_id=connector_trigger_id,
                    config=schedule_config,
                ),
                ctx=ctx,
            )
            surface.schedule_id = created_schedule.id
            surface.surface_identity_email = account.email
            return await self.surface_repository.update(surface)

        await self.schedule_service.update_schedule(
            existing_schedule_id,
            ScheduleUpdateEntity(is_active=surface.is_active),
            ctx=ctx,
        )
        surface.surface_identity_email = account.email
        return await self.surface_repository.update(surface)

    async def _delete_email_schedule_if_needed(
        self,
        surface: AgentSurfaceEntity,
    ) -> None:
        if not self._is_email_surface(surface):
            return
        if self.schedule_service is None:
            return
        schedule_id = surface.schedule_id
        if schedule_id is None:
            return
        await self.schedule_service.delete_schedule(schedule_id)

    async def _ensure_composio_email_account(
        self,
        account: SurfaceAccountInfo,
    ) -> None:
        if self._auth_config_port is None:
            raise AgentSurfaceValidationError(
                "Email surfaces require Composio auth config validation"
            )
        if account.auth_config_id is None:
            raise AgentSurfaceValidationError(
                "Email surfaces require a Composio-backed connected account"
            )
        auth_config = await self._auth_config_port.get_auth_config(
            account.auth_config_id
        )
        if auth_config is None or auth_config.provider != AuthProvider.COMPOSIO.value:
            raise AgentSurfaceValidationError(
                "Email surfaces require a Composio-backed connected account"
            )

    async def _resolve_email_connector_trigger_id(
        self, surface_type: SurfacePlatform
    ) -> str:
        if self.connector_trigger_repository is None:
            raise AgentSurfaceValidationError(
                "Connector trigger repository is not configured"
            )
        trigger_event_name = _EMAIL_TRIGGER_EVENT_TYPES.get(surface_type.value.upper(), ())
        triggers = await self.connector_trigger_repository.get_by_app_name_and_event_type(
            surface_type.value.lower(),
            trigger_event_name,
        )
        if triggers:
            return triggers[0].id
        raise AgentSurfaceValidationError(
            f"Could not find a connector trigger for {surface_type.value.lower()} email surfaces"
        )

    async def _get_connected_account(self, account_id: UUID) -> SurfaceAccountInfo:
        if self._account_port is None:
            raise AgentSurfaceValidationError(
                "Surface service account port is not configured"
            )
        account = await self._account_port.get_account(account_id)
        if account is None:
            raise AgentSurfaceValidationError(
                f"Surface account '{account_id}' not found"
            )
        return account

    def _validate_runtime_supported(self, surface: AgentSurfaceEntity) -> None:
        if surface.surface_type in {SurfacePlatform.GMAIL, SurfacePlatform.OUTLOOK}:
            return
        if public_https_api_url_available():
            return
        if (
            surface.surface_type is SurfacePlatform.TELEGRAM
            and surface_settings.enable_telegram_polling_mode
        ):
            return
        if (
            surface.surface_type is SurfacePlatform.SLACK
            and surface_settings.enable_slack_socket_mode
        ):
            return
        raise AgentSurfaceValidationError(
            f"{surface.surface_type.value} surfaces require a public HTTPS API URL "
            "for webhook delivery in this runtime. Only Telegram polling and Slack "
            "Socket Mode are supported without a public webhook URL."
        )

    async def _ensure_unique_org_credential_binding(
        self,
        surface: AgentSurfaceEntity,
    ) -> None:
        if surface.account_id is not None:
            conflict = await self.surface_repository.get_account_conflict_in_org(
                pod_id=surface.pod_id,
                account_id=surface.account_id,
                exclude_surface_id=surface.id,
            )
            if isinstance(conflict, AgentSurfaceEntity):
                raise AgentSurfaceValidationError(
                    "This connected account is already used by another surface in "
                    "this organization. Delete that surface before reusing the account."
                )
            return

        if surface.credential_mode is not SurfaceCredentialMode.SYSTEM:
            return

        conflict = await self.surface_repository.get_system_credential_conflict_in_org(
            pod_id=surface.pod_id,
            platform=surface.surface_type.value,
            exclude_surface_id=surface.id,
        )
        if isinstance(conflict, AgentSurfaceEntity):
            raise AgentSurfaceValidationError(
                f"System {surface.surface_type.value} credentials are already used "
                "by another surface in this organization. Delete that surface before "
                "enabling system credentials for another pod."
            )

    async def _ensure_unique_telegram_account(
        self,
        surface: AgentSurfaceEntity,
    ) -> None:
        if surface.account_id is None:
            return
        existing = await self.surface_repository.get_by_platform_and_account_id(
            platform=SurfacePlatform.TELEGRAM.value,
            account_id=surface.account_id,
            exclude_surface_id=surface.id,
        )
        if existing is not None:
            raise AgentSurfaceValidationError(
                "Telegram account is already connected to another surface"
            )

    async def _prepare_telegram_webhook(
        self,
        surface: AgentSurfaceEntity,
    ) -> dict[str, Any]:
        """Validate the Telegram account and mint a webhook secret.

        Returns the bot credentials so the caller can register the webhook after
        the surface (and its secret) are persisted.
        """
        credentials = await self._telegram_credentials(surface)
        self._assert_public_webhook_url_or_raise()
        surface.configure_webhook_secret(secret=secrets.token_urlsafe(32))
        return credentials

    async def _telegram_credentials(
        self, surface: AgentSurfaceEntity
    ) -> dict[str, Any]:
        if surface.account_id is None:
            raise AgentSurfaceValidationError(
                "Telegram WEBHOOK surfaces require account_id"
            )
        account = await self._get_connected_account(surface.account_id)
        if account.connector_id.lower() != "telegram":
            raise AgentSurfaceValidationError(
                "Telegram surfaces require a connected telegram account"
            )
        credentials = dict(account.credentials or {})
        if not str(credentials.get("bot_token") or "").strip():
            raise AgentSurfaceValidationError(
                "Telegram account credentials missing bot_token"
            )
        return credentials

    def _assert_public_webhook_url_or_raise(self) -> None:
        if not public_https_api_url_available():
            raise AgentSurfaceValidationError(
                "Telegram WEBHOOK surfaces require a public HTTPS api_url; "
                "localhost and http api_url values are not supported. Local native "
                "workers poll when ENABLE_TELEGRAM_POLLING_MODE=true."
            )

    def _build_public_surface_webhook_url(self, surface_id: UUID) -> str:
        self._assert_public_webhook_url_or_raise()
        base_url = settings.api_url.rstrip("/")
        return f"{base_url}/surfaces/{surface_id}/webhook"

    async def _register_telegram_webhook(
        self,
        *,
        credentials: dict[str, Any],
        webhook_url: str,
        webhook_secret: str,
    ) -> None:
        """Register the Telegram webhook idempotently.

        Clears any prior webhook and pending updates, sets the new webhook
        (restricted to the update types the surface handles), then verifies via
        getWebhookInfo. Each step retries on transient failures (429/5xx/network)
        honoring Telegram's retry_after. The surface row is already persisted by
        the caller; a hard failure here is surfaced as an actionable
        AgentSurfacePlatformError (with Telegram's real description) without
        rolling back the saved secret, so a transient hiccup self-heals on retry.
        """
        client = TelegramClient.from_credentials(credentials)
        try:
            await self._telegram_webhook_call(
                client, "deleteWebhook", {"drop_pending_updates": True}
            )
            await self._telegram_webhook_call(
                client,
                "setWebhook",
                {
                    "url": webhook_url,
                    "secret_token": webhook_secret,
                    "allowed_updates": ALLOWED_UPDATES,
                    "drop_pending_updates": True,
                },
            )
            info = await self._telegram_webhook_call(client, "getWebhookInfo", {})
        except TelegramApiError as exc:
            raise AgentSurfacePlatformError(
                "telegram",
                "Could not configure Telegram webhook automatically. Set it "
                f"manually to {webhook_url}. Telegram response: {exc.description}",
            ) from exc
        except Exception as exc:
            raise AgentSurfacePlatformError(
                "telegram",
                "Could not configure Telegram webhook automatically. Set it "
                f"manually to {webhook_url}.",
            ) from exc

        registered_url = str((info.get("result") or {}).get("url") or "")
        if registered_url != webhook_url:
            raise AgentSurfacePlatformError(
                "telegram",
                f"Telegram did not confirm the webhook URL (got '{registered_url}'). "
                f"Set it manually to {webhook_url}.",
            )

    async def _delete_telegram_webhook(self, surface: AgentSurfaceEntity) -> None:
        # Best-effort teardown: a Telegram outage must not block disabling or
        # deleting a surface.
        try:
            credentials = await self._telegram_credentials(surface)
            client = TelegramClient.from_credentials(credentials)
            await self._telegram_webhook_call(
                client, "deleteWebhook", {"drop_pending_updates": False}
            )
        except Exception as exc:
            logger.warning("Could not disable Telegram webhook: %s", exc)

    async def _telegram_webhook_call(
        self,
        client: TelegramClient,
        method: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await with_retry(
            lambda: client.call(method, payload),
            policy=_WEBHOOK_RETRY_POLICY,
            classify=classify_telegram_error,
            retry_after=telegram_retry_after,
        )

    def _is_email_surface(self, surface: AgentSurfaceEntity) -> bool:
        return surface.surface_type.is_email
