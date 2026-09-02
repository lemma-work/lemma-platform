"""Connector-backed adapters for the schedule module."""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import Any

from composio import Composio

from app.core.concurrency.offload import run_blocking
from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.domain.auth_install import ResolvedAuthInstall
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.connectors.domain.connector_trigger import ConnectorTriggerEntity
from app.modules.connectors.infrastructure.adapters.auth_provider_registry import (
    AuthProviderRegistry,
)
from app.modules.connectors.infrastructure.adapters.env_system_oauth_config import (
    EnvSystemOAuthConfigAdapter,
)
from app.modules.connectors.infrastructure.adapters.oauth_redirect_uri_builder import (
    OAuthRedirectUriBuilder,
)
from app.composition.connector_identity import (
    SqlAlchemyOrganizationAccessAdapter,
)
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.connectors.infrastructure.repositories.auth_config_repository import (
    AuthConfigRepository,
)
from app.modules.connectors.infrastructure.repositories.connect_request_repository import (
    ConnectRequestRepository,
)
from app.modules.connectors.infrastructure.repositories.connector_repository import (
    ConnectorRepository,
)
from app.modules.connectors.infrastructure.repositories.connector_trigger_repository import (
    ConnectorTriggerRepository,
)
from app.modules.connectors.services.auth.composio_auth_provider import (
    ComposioAuthProvider,
)
from app.modules.connectors.services.auth.lemma_auth_provider import LemmaAuthProvider
from app.modules.connectors.services.connector_service import ConnectorService
from app.modules.schedule.domain.errors import (
    ScheduleInfrastructureError,
    ScheduleValidationError,
)
from app.modules.schedule.domain.interfaces import (
    ExternalScheduleWriter,
    ProvisionedTrigger,
    WebhookVerifier,
)
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType

logger = get_logger(__name__)


class ComposioScheduleManager:
    """Create and remove Composio trigger subscriptions off the event loop."""

    @staticmethod
    def _client() -> Composio:
        # Shared. This ran on every trigger create and delete, each one paying
        # 42-262ms of SDK construction on the event loop.
        from app.modules.connectors.infrastructure.composio_client import (
            get_composio_client,
        )

        return get_composio_client()

    async def create_schedule(
        self,
        account: AccountEntity,
        app_trigger: ConnectorTriggerEntity,
        config: dict[str, Any],
    ) -> str:
        credentials = account.credentials
        connection_id = getattr(credentials, "connection_id", None)
        if not connection_id:
            raise ScheduleValidationError("Connector account is not active")

        def create_trigger():
            return self._client().triggers.create(
                slug=app_trigger.event_type,
                connected_account_id=connection_id,
                trigger_config=config or {},
            )

        try:
            response = await run_blocking(create_trigger, limiter="external_http")
        except Exception as exc:
            logger.debug(
                "runtime.schedule_connectors.composio_trigger_creation.diagnostic",
                error_type=type(exc).__name__,
            )
            raise ScheduleInfrastructureError(
                "Connector trigger creation failed"
            ) from exc
        return response.trigger_id

    async def delete_schedule(self, account: AccountEntity, provider_id: str) -> None:
        del account
        try:
            await run_blocking(
                self._client().triggers.delete, provider_id, limiter="external_http"
            )
        except Exception as exc:
            logger.debug(
                "runtime.schedule_connectors.composio_trigger_deletion.diagnostic",
                error_type=type(exc).__name__,
            )
            raise ScheduleInfrastructureError(
                "Connector trigger deletion failed"
            ) from exc

    async def get_schedule(
        self, account: AccountEntity, provider_id: str
    ) -> object | None:
        del account
        try:
            return await run_blocking(
                self._client().triggers.get, provider_id, limiter="external_http"
            )
        except Exception as exc:
            logger.debug(
                "runtime.schedule_connectors.composio_trigger_lookup.observed",
                error_type=type(exc).__name__,
            )
            return None


class ManagersFactory:
    @staticmethod
    def get_manager(
        app_trigger: ConnectorTriggerEntity,
        auth_provider: str,
        install: ResolvedAuthInstall | None = None,
    ) -> ComposioScheduleManager | None:
        del app_trigger
        if install is not None and install.composio_toolkit_slug:
            return ComposioScheduleManager()
        if auth_provider == AuthProvider.COMPOSIO.value:
            return ComposioScheduleManager()
        return None


def _github_binding(
    trigger: ConnectorTriggerEntity, account: AccountEntity
) -> dict[str, Any]:
    """The routing key for a GitHub trigger, taken from what is already known.

    Nothing here is something a person could sensibly type into a form. The
    installation id lives on the account -- it is what the App install
    redirected back with -- and the event is the trigger they picked. Asking for
    either would be asking someone to copy a number out of a URL, and getting it
    wrong routes another organization's events at their pod.
    """
    if not account.external_ref:
        from app.modules.connectors.services.auth.github_installation import install_url

        where = install_url()
        raise ScheduleValidationError(
            "This GitHub account is not bound to an App installation, so there "
            "is nothing to route events from. "
            + (
                f"Install the app at {where}, then reconnect the account."
                if where
                else "Install the app on the organization, then reconnect it."
            )
        )
    return {
        "source": "github",
        "installation_id": str(account.external_ref),
        "event": trigger.event_type,
    }


# Connectors whose triggers need no remote subscription, only a routing key.
# Absence from both this table and `ManagersFactory` is an error, not a shrug.
_LOCAL_BINDERS: dict[str, Callable[[ConnectorTriggerEntity, AccountEntity], dict]] = {
    "github": _github_binding,
}


class ExternalScheduleWriterAdapter(ExternalScheduleWriter):
    """Provision provider triggers behind the schedule-owned writer port."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        connector_service: ConnectorService | None = None,
        connector_trigger_repository: ConnectorTriggerRepository | None = None,
    ) -> None:
        self.uow = uow
        self._connector_service = connector_service
        self._connector_trigger_repository = connector_trigger_repository

    def _build_connector_service(self) -> ConnectorService:
        connector_repository = ConnectorRepository(self.uow)
        encryption = get_secret_cipher()
        return ConnectorService(
            uow=self.uow,
            connector_repository=connector_repository,
            auth_config_repository=AuthConfigRepository(
                self.uow, encryption=encryption
            ),
            account_repository=AccountRepository(self.uow, encryption=encryption),
            connect_request_repository=ConnectRequestRepository(self.uow),
            auth_provider_registry=AuthProviderRegistry(
                {
                    AuthProvider.LEMMA.value: LemmaAuthProvider(),
                    AuthProvider.COMPOSIO.value: ComposioAuthProvider(
                        connector_repository=connector_repository
                    ),
                }
            ),
            redirect_uri_builder=OAuthRedirectUriBuilder(),
            organization_access=SqlAlchemyOrganizationAccessAdapter(self.uow),
            system_oauth_config=EnvSystemOAuthConfigAdapter(),
        )

    @property
    def connector_service(self) -> ConnectorService:
        if self._connector_service is None:
            self._connector_service = self._build_connector_service()
        return self._connector_service

    @property
    def connector_trigger_repository(self) -> ConnectorTriggerRepository:
        if self._connector_trigger_repository is None:
            self._connector_trigger_repository = ConnectorTriggerRepository(self.uow)
        return self._connector_trigger_repository

    async def _resolve_manager(self, schedule: ScheduleEntity):
        if not schedule.connector_trigger_id or not schedule.account_id:
            return None, None, None
        trigger = await self.connector_trigger_repository.get(
            schedule.connector_trigger_id
        )
        if trigger is None:
            raise ScheduleValidationError("Connector trigger not found")
        account = await self.connector_service.get_account(
            schedule.account_id,
            schedule.user_id,
        )
        if account.connector_id != trigger.connector_id:
            raise ScheduleValidationError("Account does not match trigger connector")
        auth_config = await self.connector_service.auth_config_repository.get(
            account.auth_config_id
        )
        if auth_config is None:
            raise ScheduleValidationError("Account auth configuration not found")
        connector = await self.connector_service.get_connector(account.connector_id)
        auth_install = self.connector_service._resolve_auth_install(
            connector, auth_config
        )
        provider = getattr(auth_config.provider, "value", str(auth_config.provider))
        return (
            ManagersFactory.get_manager(
                trigger,
                provider,
                install=auth_install,
            ),
            account,
            trigger,
        )

    async def create_provider_trigger(
        self, schedule: ScheduleEntity
    ) -> ProvisionedTrigger:
        if schedule.schedule_type is not ScheduleType.WEBHOOK:
            return ProvisionedTrigger()
        manager, account, trigger = await self._resolve_manager(schedule)
        if account is None or trigger is None:
            # The schedule names no connector trigger, so it is routed by
            # whatever its author put in `config` and there is nothing to
            # provision on anyone's behalf.
            return ProvisionedTrigger()
        if manager is None:
            binder = _LOCAL_BINDERS.get(trigger.connector_id)
            if binder is None:
                # This is the case that used to return None and look like
                # success. The schedule row would exist, nothing would be
                # subscribed, and it could never fire.
                raise ScheduleValidationError(
                    f"'{trigger.connector_id}' triggers cannot be provisioned: "
                    "no provider subscription can be created for them and no "
                    "local routing key is defined, so the schedule would never "
                    "fire."
                )
            return ProvisionedTrigger(bound_config=binder(trigger, account))
        provider_id = await manager.create_schedule(
            account=account,
            app_trigger=trigger,
            config=schedule.config,
        )
        return ProvisionedTrigger(provider_trigger_id=provider_id)

    async def delete_provider_trigger(self, schedule: ScheduleEntity) -> None:
        if schedule.schedule_type is not ScheduleType.WEBHOOK:
            return
        provider_id = schedule.config.get("provider_trigger_id")
        if not provider_id:
            return
        manager, account, _trigger = await self._resolve_manager(schedule)
        if manager is not None and account is not None:
            await manager.delete_schedule(account, str(provider_id))


@lru_cache(maxsize=1)
def _webhook_verification_client() -> Composio:
    """The SDK client used to verify inbound webhooks, built once.

    Construction is not free -- it reads config, builds an httpx client and
    imports the SDK's lazy namespaces -- and was measured at 76ms cold / 4ms
    warm at the neighbouring call site in ``composio_auth_provider``. Doing it
    per delivery put that on the event loop at a rate the sender picks.

    Cached as an object singleton, which is the sanctioned exception to
    "caching goes through Redis": this is a client handle, not data.
    """
    return Composio(
        api_key=connector_settings.composio_api_key or "webhook-verification"
    )


class ComposioWebhookVerifier(WebhookVerifier):
    async def verify(self, payload: str, headers: dict[str, Any]) -> dict[str, Any]:
        secret = connector_settings.composio_webhook_secret
        if not secret:
            raise ScheduleInfrastructureError(
                "Connector webhook verification is not configured"
            )

        def _verify() -> dict[str, Any]:
            return _webhook_verification_client().triggers.verify_webhook(
                id=headers.get("webhook-id", ""),
                payload=payload,
                signature=headers.get("webhook-signature", ""),
                timestamp=headers.get("webhook-timestamp", ""),
                secret=secret,
            )

        # `external_http` rather than `cpu_bound`: this is the blocking-network-SDK
        # class, and it shares its limiter with every other Composio call so a
        # burst of webhooks cannot starve the CPU pool that chunking and zipping
        # depend on.
        return await run_blocking(_verify, limiter="external_http")
