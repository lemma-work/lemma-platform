"""Connector-backed adapters for the schedule module."""

from __future__ import annotations

import asyncio
from typing import Any

from composio import Composio

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.domain.connector import AuthProvider, ConnectorEntity
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
    WebhookVerifier,
)
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType

logger = get_logger(__name__)


class ComposioScheduleManager:
    """Create and remove Composio trigger subscriptions off the event loop."""

    @staticmethod
    def _client() -> Composio:
        return Composio(api_key=connector_settings.composio_api_key)

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
            response = await asyncio.to_thread(create_trigger)
        except Exception as exc:
            logger.debug(
                'runtime.schedule_connectors.composio_trigger_creation.diagnostic',
                error_type=type(exc).__name__,
            )
            raise ScheduleInfrastructureError(
                "Connector trigger creation failed"
            ) from exc
        return response.trigger_id

    async def delete_schedule(self, account: AccountEntity, provider_id: str) -> None:
        del account
        try:
            await asyncio.to_thread(self._client().triggers.delete, provider_id)
        except Exception as exc:
            logger.debug(
                'runtime.schedule_connectors.composio_trigger_deletion.diagnostic',
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
            return await asyncio.to_thread(self._client().triggers.get, provider_id)
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
        connector: ConnectorEntity | None = None,
    ) -> ComposioScheduleManager | None:
        del app_trigger
        if connector is not None and connector.composio_toolkit_slug:
            return ComposioScheduleManager()
        if auth_provider == AuthProvider.COMPOSIO.value:
            return ComposioScheduleManager()
        return None


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
        effective_connector = self.connector_service._build_effective_connector(
            connector, auth_config
        )
        provider = getattr(auth_config.provider, "value", str(auth_config.provider))
        return (
            ManagersFactory.get_manager(
                trigger,
                provider,
                connector=effective_connector,
            ),
            account,
            trigger,
        )

    async def create_provider_trigger(self, schedule: ScheduleEntity) -> str | None:
        if schedule.schedule_type is not ScheduleType.WEBHOOK:
            return None
        manager, account, trigger = await self._resolve_manager(schedule)
        if manager is None or account is None or trigger is None:
            return None
        return await manager.create_schedule(
            account=account,
            app_trigger=trigger,
            config=schedule.config,
        )

    async def delete_provider_trigger(self, schedule: ScheduleEntity) -> None:
        if schedule.schedule_type is not ScheduleType.WEBHOOK:
            return
        provider_id = schedule.config.get("provider_trigger_id")
        if not provider_id:
            return
        manager, account, _trigger = await self._resolve_manager(schedule)
        if manager is not None and account is not None:
            await manager.delete_schedule(account, str(provider_id))


class ComposioWebhookVerifier(WebhookVerifier):
    def verify(self, payload: str, headers: dict[str, Any]) -> dict[str, Any]:
        secret = connector_settings.composio_webhook_secret
        if not secret:
            raise ScheduleInfrastructureError(
                "Connector webhook verification is not configured"
            )
        composio = Composio(
            api_key=connector_settings.composio_api_key or "webhook-verification"
        )
        return composio.triggers.verify_webhook(
            id=headers.get("webhook-id", ""),
            payload=payload,
            signature=headers.get("webhook-signature", ""),
            timestamp=headers.get("webhook-timestamp", ""),
            secret=secret,
        )
