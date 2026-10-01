from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select

from app.modules.connectors.infrastructure.repositories.catalog_rows import (
    aconvert_valid_rows,
)
from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.repository import SqlAlchemyRepository
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigStatus,
)
from app.modules.connectors.infrastructure.models import AuthConfig
from app.modules.connectors.infrastructure.repositories.connector_secrets import (
    AUTH_CONFIG_PURPOSE,
    AUTH_CONFIGS_TABLE,
    auth_config_scope,
)
from app.modules.vault.contracts import JsonObject, Revealed, Vault, vault_for


class AuthConfigRepository(
    SqlAlchemyRepository[AuthConfig, AuthConfigEntity],
):
    """Repository for installs.

    The install's config -- client secrets and signing secrets beside public
    endpoints -- is one vault secret, revealed on every read that returns an
    entity. Deleting the row deletes the secret (a trigger does it).
    """

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        message_bus: MessageBus | None = None,
        vault: Vault | None = None,
    ):
        super().__init__(uow, AuthConfig, AuthConfigEntity)
        self._vault = vault or vault_for(uow)
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    def _to_model(self, entity: AuthConfigEntity) -> AuthConfig:
        data = entity.model_dump(exclude_unset=True)
        # `provider`/`provider_config` are read-only compatibility views on the
        # entity; the column is `kind`. Dropped rather than renamed, because the
        # entity has already resolved them. The config goes to the vault.
        data.pop("provider", None)
        data.pop("provider_config", None)
        data.pop("config", None)
        data["kind"] = entity.kind.value
        data["metadata_"] = data.pop("metadata", None)
        return self.model_cls(**data)

    async def _to_entity(self, instance: AuthConfig) -> AuthConfigEntity:
        secret = None
        if instance.config_secret_id is not None:
            secret = await self._vault.reveal(
                instance.config_secret_id,
                expect=auth_config_scope(instance.organization_id),
                purpose=AUTH_CONFIG_PURPOSE,
            )
        return self._entity_from(instance, secret)

    @staticmethod
    def _entity_from(instance: AuthConfig, secret: Revealed | None) -> AuthConfigEntity:
        entity = instance.to_entity()
        entity.config = secret.json() if secret is not None else None
        return entity

    async def _write_config(
        self, instance: AuthConfig, config: JsonObject | None
    ) -> None:
        """Point ``instance`` at a secret holding ``config``, or at none.

        An empty config is no secret -- there is nothing in it to protect -- so
        it is stored as no pointer and reads back as ``None``. An unchanged
        config writes nothing, so saving an install's name does not mint a new
        version of its secrets.
        """
        scope = auth_config_scope(instance.organization_id)
        if not config:
            # The trigger deletes the orphaned vault row with the column.
            instance.config_secret_id = None
            return
        if instance.config_secret_id is not None:
            await self._vault.replace(
                instance.config_secret_id,
                expect=scope,
                purpose=AUTH_CONFIG_PURPOSE,
                value=config,
                skip_if_equal=True,
            )
            return
        ref = await self._vault.put(
            scope=scope,
            purpose=AUTH_CONFIG_PURPOSE,
            value=config,
            owner_table=AUTH_CONFIGS_TABLE,
        )
        instance.config_secret_id = ref.id

    async def create(self, entity: AuthConfigEntity) -> AuthConfigEntity:
        instance = self._to_model(entity)
        await self._write_config(instance, entity.config)
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
        instance.kind = entity.kind.value
        instance.config_source = (
            entity.config_source.value
            if hasattr(entity.config_source, "value")
            else str(entity.config_source)
        )
        instance.status = (
            entity.status.value
            if hasattr(entity.status, "value")
            else str(entity.status)
        )
        await self._write_config(instance, entity.config)
        instance.is_default = entity.is_default
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
        """The install a bare connector id resolves to: the default one.

        An organization may hold many active installs of one connector -- the
        model says so, and says there is deliberately no
        `(organization_id, connector_id)` uniqueness -- so "the" install for a
        connector id needs a rule. There was none: no `is_default` predicate
        and no ORDER BY, so this returned whatever the planner produced first,
        which is heap order and changes after a VACUUM or an update.

        Two things followed. The same call could authenticate against a
        different install on different days. And `_clear_default_install`,
        which demotes the current default before promoting a new one, would be
        handed a non-default row, return early leaving the real default in
        place, and the promotion then violated
        `uq_auth_configs_default_per_connector` -- an unhandled IntegrityError,
        reachable whenever an org had three active installs of one connector.

        Ordering by `is_default` makes this agree with the partial unique index
        that already names one row per (org, connector), and with every other
        resolver on both sides of the API. `created_at` only breaks a tie among
        rows that are all non-default, so the answer is at least stable.
        """
        stmt = (
            select(AuthConfig)
            .where(
                AuthConfig.organization_id == organization_id,
                AuthConfig.connector_id == connector_id,
                AuthConfig.status == AuthConfigStatus.ACTIVE.value,
            )
            .order_by(AuthConfig.is_default.desc(), AuthConfig.created_at.asc())
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
        # One vault read for the page, not one per install.
        revealed = await self._vault.reveal_many(
            {
                instance.config_secret_id: auth_config_scope(instance.organization_id)
                for instance in instances
                if instance.config_secret_id is not None
            },
            purpose=AUTH_CONFIG_PURPOSE,
        )

        async def to_entity(instance: AuthConfig) -> AuthConfigEntity:
            secret_id = instance.config_secret_id
            return self._entity_from(
                instance, revealed.get(secret_id) if secret_id is not None else None
            )

        return await aconvert_valid_rows(instances, to_entity), next_cursor

    async def delete(self, id: UUID) -> bool:
        stmt = select(AuthConfig).where(AuthConfig.id == id)
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        if not instance:
            return False
        await self.session.delete(instance)
        await self.session.flush()
        return True
