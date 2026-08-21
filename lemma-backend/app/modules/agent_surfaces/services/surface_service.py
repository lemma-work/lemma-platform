from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID


from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.platforms.common import (
    public_https_api_url_available,
)
from app.modules.agent_surfaces.platforms.telegram.mode import (
    telegram_requires_webhook_setup,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfaceEventMode,
    SurfaceMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceAlreadyExistsError,
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
from app.composition.surface_connectors import (
    ConnectorTriggerRepository,
)
from app.composition.surface_schedule import ScheduleService
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.services.credential_uniqueness import (
    ensure_unique_org_credential_binding,
)
from app.modules.agent_surfaces.services.event_receiver_service import (
    notify_surface_receiver_config_changed,
)
from app.modules.agent_surfaces.services.telegram_mini_app_mixin import (
    TelegramMiniAppSyncMixin,
)
from app.modules.agent_surfaces.services.surface_setup_read import (
    SurfaceSetupReadMixin,
)
from app.modules.agent_surfaces.services.surface_email_schedule import (
    SurfaceEmailScheduleMixin,
)
from app.modules.agent_surfaces.services.surface_telegram_webhook import (
    SurfaceTelegramWebhookMixin,
    _telegram_transition,
)
from app.modules.agent_surfaces.services.surface_consent import (
    SurfaceConsentMixin,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from app.core.authorization.context import Context
    from app.modules.agent_surfaces.domain.models import SurfaceChannelInfo
    from app.modules.agent_surfaces.services.credential_resolver import (
        SurfaceCredentialResolver,
    )
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"

# Shared Redis cache of Teams admin-consent probe results (per-entry TTL: 60 s
# granted / 10 s denied), so the Graph probe is shared across replicas. Redis
# unavailable -> re-probe (never fails).
_consent_check_cache: RedisJsonCache | None = None


class AgentSurfaceService(
    SurfaceConsentMixin,
    SurfaceTelegramWebhookMixin,
    SurfaceEmailScheduleMixin,
    SurfaceSetupReadMixin,
    TelegramMiniAppSyncMixin,
):
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
        surface_identity_email: str | None = None,
        ctx: Context | None = None,
    ) -> AgentSurfaceEntity:
        # A surface is addressed by its pod-unique name (defaults to the
        # platform); several surfaces of the same platform can coexist under
        # distinct names (e.g. different bots → different agents). Distinct bot
        # accounts are still enforced by the credential/account conflict checks
        # below.
        resolved_name = (name or "").strip() or AgentSurfaceEntity.default_name_for(
            platform
        )
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
        # Resend is a system-credentialed email surface: it needs an inbound
        # address that routing matches on and outbound uses as the From. (Other
        # email surfaces get this from their connected account.) Callers that
        # allocate a readable per-agent address pass it in; the pod-level
        # fallback derives one that cannot collide.
        if platform is SurfacePlatform.RESEND and not entity.surface_identity_email:
            entity.surface_identity_email = (
                surface_identity_email or self._provision_resend_address(pod_id)
            )
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
        synced = await self._sync_email_schedule(
            created, previous_surface=None, ctx=ctx
        )
        await notify_surface_receiver_config_changed(synced.id)
        return synced

    @staticmethod
    def _provision_resend_address(pod_id: UUID) -> str:
        """Derive a unique per-pod inbound/outbound Resend address.

        Uses the pod id for uniqueness under a catch-all inbound domain
        (``*@<domain>`` → one webhook), so no per-address API registration. The
        full 32-char hex is used (not a prefix) so two pods can never collide on
        the same address — ``get_active_by_address`` would otherwise misroute one
        pod's inbound mail to the other. ``pod-`` + 32 hex = 36 chars, within the
        64-char local-part limit.
        """
        domain = surface_settings.resend_inbound_domain
        if not domain:
            raise AgentSurfaceValidationError(
                "Email is not configured for this deployment: set "
                "RESEND_INBOUND_DOMAIN to a verified catch-all domain."
            )
        return f"pod-{pod_id.hex}@{domain}"

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

        if update_agent_id:
            surface.update_agent(agent_id)

        # Any one of these touches the account binding, and the binding has to be
        # re-resolved as a whole rather than field by field.
        binding_changes = (
            config,
            account_id,
            mode,
            event_mode,
            credential_mode,
            external_workspace_id,
            external_tenant_id,
            external_channel_id,
        )
        if any(value is not None for value in binding_changes):
            await self._apply_binding_update(
                surface,
                config=config,
                account_id=account_id,
                mode=mode,
                event_mode=event_mode,
                credential_mode=credential_mode,
                external_workspace_id=external_workspace_id,
                external_tenant_id=external_tenant_id,
                external_channel_id=external_channel_id,
            )
        if is_active is not None:
            surface.toggle_active(is_active)

        telegram = _telegram_transition(previous_surface, surface)
        telegram_credentials: dict[str, Any] | None = None
        if telegram.register:
            await self._ensure_unique_telegram_account(surface)
            telegram_credentials = await self._prepare_telegram_webhook(surface)
        if telegram.disable:
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

    async def _apply_binding_update(
        self,
        surface: AgentSurfaceEntity,
        *,
        config: SurfaceConfig | None,
        account_id: UUID | None,
        mode: SurfaceMode | None,
        event_mode: SurfaceEventMode | None,
        credential_mode: SurfaceCredentialMode | None,
        external_workspace_id: str | None,
        external_tenant_id: str | None,
        external_channel_id: str | None,
    ) -> None:
        """Re-resolve the account binding, then write the changed fields onto it."""
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
        failure_count = 0
        cursor: UUID | None = None
        while True:
            surfaces, cursor = await self.list_surfaces_by_pod(pod_id, cursor=cursor)
            for surface in surfaces:
                try:
                    await self.delete_surface(surface.id)
                    deleted += 1
                except Exception:
                    failure_count += 1
            if cursor is None:
                break
        if failure_count:
            logger.error(
                "surface.cleanup.failed", pod_id=pod_id, failure_count=failure_count
            )
        return deleted

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
        if (
            surface.surface_type is SurfacePlatform.RESEND
            and surface_settings.enable_resend_polling_mode
        ):
            # Resend sends outbound over its API and, in polling mode, pulls
            # inbound from its received-emails API — neither needs a public
            # callback, so a localhost/desktop runtime can run an email surface.
            return
        raise AgentSurfaceValidationError(
            f"{surface.surface_type.value} surfaces require a public HTTPS API URL "
            "for webhook delivery in this runtime. Only Telegram polling, Slack "
            "Socket Mode, and Resend polling are supported without a public "
            "webhook URL."
        )

    async def _ensure_unique_org_credential_binding(
        self,
        surface: AgentSurfaceEntity,
    ) -> None:
        await ensure_unique_org_credential_binding(
            surface, surface_repository=self.surface_repository
        )

    def _is_email_surface(self, surface: AgentSurfaceEntity) -> bool:
        return surface.surface_type.is_email
