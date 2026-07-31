from __future__ import annotations

from typing import Sequence
from uuid import UUID

from sqlalchemy import select

from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.repository import SqlAlchemyRepository
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigStatus,
)
from app.modules.connectors.domain.ports import SecretEncryptionPort
from app.modules.connectors.infrastructure.models import AuthConfig


class AuthConfigRepository(
    SqlAlchemyRepository[AuthConfig, AuthConfigEntity],
):
    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        encryption: SecretEncryptionPort,
        message_bus: MessageBus | None = None,
    ):
        super().__init__(uow, AuthConfig, AuthConfigEntity)
        self.encryption = encryption
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    async def _to_model(self, entity: AuthConfigEntity) -> AuthConfig:
        data = entity.model_dump(exclude_unset=True)
        data["provider_config"] = await self.encryption.encrypt_json_async(
            entity.provider_config
        )
        data["metadata_"] = data.pop("metadata", None)
        return self.model_cls(**data)

    async def _to_entity(self, instance: AuthConfig) -> AuthConfigEntity:
        entity = instance.to_entity()
        entity.provider_config = await self.encryption.decrypt_json_async(
            entity.provider_config
        )
        return entity

    async def create(self, entity: AuthConfigEntity) -> AuthConfigEntity:
        instance = await self._to_model(entity)
        self.session.add(instance)
        await self.session.flush()
        return await self._to_entity(instance)

    async def update(self, entity: AuthConfigEntity) -> AuthConfigEntity:
        stmt = select(AuthConfig).where(AuthConfig.id == entity.id)
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        if not instance:
            raise ValueError(f"AuthConfig {entity.id} not found")

        instance.name = entity.name
        instance.provider = entity.provider.value if hasattr(entity.provider, "value") else str(entity.provider)
        instance.config_source = (
            entity.config_source.value
            if hasattr(entity.config_source, "value")
            else str(entity.config_source)
        )
        instance.status = entity.status.value if hasattr(entity.status, "value") else str(entity.status)
        instance.provider_config = await self.encryption.encrypt_json_async(
            entity.provider_config
        )
        instance.metadata_ = entity.metadata
        instance.updated_by_user_id = entity.updated_by_user_id
        await self.session.flush()
        return await self._to_entity(instance)

    async def get(self, id: UUID) -> AuthConfigEntity | None:
        stmt = select(AuthConfig).where(AuthConfig.id == id)
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return await self._to_entity(instance) if instance else None

    async def get_active_by_org_and_app(
        self, organization_id: UUID, connector_id: str
    ) -> AuthConfigEntity | None:
        stmt = select(AuthConfig).where(
            AuthConfig.organization_id == organization_id,
            AuthConfig.connector_id == connector_id,
            AuthConfig.status == AuthConfigStatus.ACTIVE.value,
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return await self._to_entity(instance) if instance else None

    async def get_active_by_org_and_name(
        self, organization_id: UUID, name: str
    ) -> AuthConfigEntity | None:
        stmt = select(AuthConfig).where(
            AuthConfig.organization_id == organization_id,
            AuthConfig.name == name,
            AuthConfig.status == AuthConfigStatus.ACTIVE.value,
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return await self._to_entity(instance) if instance else None

    async def list_by_org(
        self,
        organization_id: UUID,
        limit: int = 100,
        cursor: UUID | None = None,
    ) -> tuple[Sequence[AuthConfigEntity], UUID | None]:
        stmt = select(AuthConfig).where(AuthConfig.organization_id == organization_id)
        if cursor is not None:
            stmt = stmt.where(AuthConfig.id > cursor)
        stmt = stmt.order_by(AuthConfig.id).limit(limit + 1)
        result = await self.session.execute(stmt)
        instances = list(result.scalars().all())
        next_cursor = None
        if len(instances) > limit:
            next_cursor = instances[limit - 1].id
            instances = instances[:limit]
        return [await self._to_entity(instance) for instance in instances], next_cursor

    async def delete(self, id: UUID) -> bool:
        stmt = select(AuthConfig).where(AuthConfig.id == id)
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        if not instance:
            return False
        await self.session.delete(instance)
        await self.session.flush()
        return True
