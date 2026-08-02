from typing import Optional
from uuid import UUID

from app.modules.connectors.domain.connector_trigger import ConnectorTriggerEntity
from app.modules.connectors.domain.errors import (
    ConnectorNotFoundError,
    ConnectorTriggerNotFoundError,
)
from app.modules.connectors.domain.ports import (
    ConnectorRepositoryPort,
    ConnectorTriggerRepositoryPort,
)
from app.modules.connectors.services.connector_service import ConnectorService


class ConnectorTriggerService:
    """Service for trigger definitions available per connector."""

    def __init__(
        self,
        *,
        trigger_repository: ConnectorTriggerRepositoryPort,
        connector_repository: ConnectorRepositoryPort,
        connector_service: ConnectorService | None = None,
    ):
        self.trigger_repository = trigger_repository
        self.connector_repository = connector_repository
        self.connector_service = connector_service

    async def _resolve_auth_config_context(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        auth_config_name: str,
    ):
        if self.connector_service is None:
            raise ConnectorNotFoundError(auth_config_name)
        auth_config = await self.connector_service.get_auth_config_by_name(
            user_id=user_id,
            organization_id=organization_id,
            auth_config_name=auth_config_name,
        )
        return auth_config, auth_config.connector_id, auth_config.kind.value

    async def list_triggers_for_auth_config(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        auth_config_name: str,
        search_query: Optional[str] = None,
        limit: int = 100,
    ) -> list[ConnectorTriggerEntity]:
        _auth_config, connector_id, kind = await self._resolve_auth_config_context(
            user_id=user_id,
            organization_id=organization_id,
            auth_config_name=auth_config_name,
        )
        triggers = await self.trigger_repository.list_by_connector_kind(
            connector_id,
            kind,
            search_query=search_query,
            limit=limit,
        )
        return list(triggers)

    async def get_trigger_for_auth_config(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        auth_config_name: str,
        trigger_name: str,
    ) -> ConnectorTriggerEntity:
        _auth_config, connector_id, kind = await self._resolve_auth_config_context(
            user_id=user_id,
            organization_id=organization_id,
            auth_config_name=auth_config_name,
        )
        trigger = await self.trigger_repository.get_by_connector_kind_and_name(
            connector_id,
            kind,
            trigger_name,
        )
        if not trigger:
            raise ConnectorTriggerNotFoundError(trigger_name)
        return trigger

    async def list_triggers(
        self,
        connector_id: Optional[str] = None,
        search_query: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> tuple[list[ConnectorTriggerEntity], Optional[str]]:
        if connector_id is not None:
            connector = await self.connector_repository.get(connector_id)
            if not connector:
                raise ConnectorNotFoundError(connector_id)

        triggers, next_cursor = await self.trigger_repository.list_all(
            connector_id=connector_id,
            search_query=search_query,
            limit=limit,
            cursor=cursor,
        )
        return list(triggers), next_cursor

    async def get_trigger(self, trigger_id: str) -> ConnectorTriggerEntity:
        trigger = await self.trigger_repository.get(trigger_id)
        if not trigger:
            raise ConnectorTriggerNotFoundError(trigger_id)
        return trigger
