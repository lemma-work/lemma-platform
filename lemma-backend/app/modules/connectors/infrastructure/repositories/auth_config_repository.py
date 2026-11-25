from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.modules.connectors.infrastructure.repositories.catalog_rows import (
    aconvert_valid_rows,
)
from app.core.infrastructure.db.integrity import violated_constraint
from app.core.infrastructure.db.repository import SqlAlchemyRepository
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigStatus,
)
from app.modules.connectors.domain.errors import ConnectorConflictError
from app.modules.connectors.infrastructure.models import AuthConfig
from app.modules.connectors.infrastructure.repositories.connector_secrets import (
    AUTH_CONFIG_PURPOSE,
    AUTH_CONFIGS_TABLE,
    auth_config_scope,
)
from app.modules.vault.contracts import (
    JsonObject,
    Revealed,
    Vault,
    legacy_json,
    vault_for,
)

#: One active install per name in an organization: the name is how a pod, an
#: agent's toolset and the API ask for it.
_NAME_TAKEN = "ix_auth_configs_unique_active_org_name"
#: One default install per connector in an organization: what a bare
#: connector id resolves to.
_DEFAULT_TAKEN = "uq_auth_configs_default_per_connector"


class AuthConfigRepository(
    SqlAlchemyRepository[AuthConfig, AuthConfigEntity],
):
    """Repository for installs.

    The install's config -- client secrets and signing secrets beside public
    endpoints -- is one vault secret, revealed on every read that returns an
    entity. Deleting the row deletes the secret (a trigger does it). A row a
    process on the previous release wrote mid-rollout has its config only in
    the old column; it is read from there and moves on its next write.
    """

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        vault: Vault | None = None,
    ):
        super().__init__(uow, AuthConfig, AuthConfigEntity)
        self._vault = vault or vault_for(self.session)

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
        return await self._entity_from(instance, secret)

    @staticmethod
    async def _entity_from(
        instance: AuthConfig, secret: Revealed | None
    ) -> AuthConfigEntity:
        entity = instance.to_entity()
        if instance.config_secret_id is not None:
            entity.config = secret.json() if secret is not None else None
        else:
            entity.config = await legacy_json(instance.config)
        return entity

    async def _write_config(
        self, instance: AuthConfig, config: JsonObject | None
    ) -> None:
        """Point ``instance`` at a secret holding ``config``, or at none, and
        clear the old column.

        An empty config is no secret -- there is nothing in it to protect -- so
        it is stored as no pointer and reads back as ``None``. An unchanged
        config writes no new version, so saving an install's name does not
        mint one.
        """
        instance.config = None
        scope = auth_config_scope(instance.organization_id)
        if not config:
            # The release trigger deletes the orphaned vault row.
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

    async def _drop_unowned(self, instance: AuthConfig) -> None:
        if instance.config_secret_id is not None:
            await self._vault.delete(
                instance.config_secret_id,
                expect=auth_config_scope(instance.organization_id),
                purpose=AUTH_CONFIG_PURPOSE,
            )

    async def create(self, entity: AuthConfigEntity) -> AuthConfigEntity:
        """The new install. Raises ``ConnectorConflictError`` for a name an
        active install of the organization already has."""
        instance = self._to_model(entity)
        # Before the savepoint, not in it: a first vault write in a process may
        # load keys, which hands the connection back -- not possible mid-savepoint.
        await self._write_config(instance, entity.config)
        try:
            async with self._name_must_be_free(entity.name):
                self.session.add(instance)
                await self.session.flush()
        except ConnectorConflictError:
            # The row was refused; its secret must not outlive it in a caller
            # that catches the conflict and commits.
            await self._drop_unowned(instance)
            raise
        return await self._to_entity(instance)

    async def update(self, entity: AuthConfigEntity) -> AuthConfigEntity:
        stmt = select(AuthConfig).where(AuthConfig.id == entity.id)
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        if not instance:
            raise ValueError(f"AuthConfig {entity.id} not found")

        # First, while nothing else on the row is pending: the vault's row read
        # would autoflush a rename outside the savepoint that reports it.
        await self._write_config(instance, entity.config)
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
        instance.is_default = entity.is_default
        instance.metadata_ = entity.metadata
        instance.updated_by_user_id = entity.updated_by_user_id
        # A rename onto a taken name, or reactivating an install whose name
        # another has since taken, is refused the same way as a create.
        async with self._name_must_be_free(entity.name):
            await self.session.flush()
        return await self._to_entity(instance)

    @asynccontextmanager
    async def _name_must_be_free(self, name: str) -> AsyncIterator[None]:
        """Write inside a savepoint; a clash on the install name -- or on
        being the connector's default, which two first installs at once both
        claim -- is a conflict.

        Left to the unique index rather than looked up first: two requests
        naming the same install at once both find the name free.
        """
        try:
            async with self.session.begin_nested():
                yield
        except IntegrityError as error:
            constraint = violated_constraint(error)
            if constraint == _DEFAULT_TAKEN:
                # Two first installs of one connector at once both decided to
                # be its default; the other got there first. A conflict, not a
                # 500: the caller can read the install that won.
                raise ConnectorConflictError(
                    "Another install of this connector became the "
                    "organization's default at the same moment. Try again.",
                    details={"name": name},
                ) from None
            if constraint != _NAME_TAKEN:
                raise
            raise ConnectorConflictError(
                f"An active connector install is already called {name!r}. "
                "Give this one a different name.",
                details={"name": name},
            ) from None

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

    async def get_active_by_org_app_and_source(
        self, organization_id: UUID, connector_id: str, config_source: str
    ) -> AuthConfigEntity | None:
        """The org's active install of this connector from this source -- Lemma's
        own app (``SYSTEM_DEFAULT``) rather than one the org brought. Ordered as
        `get_active_by_org_and_app` is, so the answer is stable."""
        stmt = (
            select(AuthConfig)
            .where(
                AuthConfig.organization_id == organization_id,
                AuthConfig.connector_id == connector_id,
                AuthConfig.config_source == config_source,
                AuthConfig.status == AuthConfigStatus.ACTIVE.value,
            )
            .order_by(AuthConfig.is_default.desc(), AuthConfig.created_at.asc())
            .limit(1)
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

    async def get_disabled_by_org_and_app(
        self, organization_id: UUID, connector_id: str
    ) -> AuthConfigEntity | None:
        """An install of this connector an admin switched off, if there is one:
        what stops a first connect from quietly installing it again."""
        stmt = (
            select(AuthConfig)
            .where(
                AuthConfig.organization_id == organization_id,
                AuthConfig.connector_id == connector_id,
                AuthConfig.status == AuthConfigStatus.DISABLED.value,
            )
            .order_by(AuthConfig.id)
            .limit(1)
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
            return await self._entity_from(
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
