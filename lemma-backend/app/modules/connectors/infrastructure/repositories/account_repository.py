from typing import Optional, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.repository import SqlAlchemyRepository
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.domain.errors import (
    AccountAlreadyConnectedError,
    AccountNotFoundError,
)
from app.modules.connectors.domain.ports import AccountRepositoryPort, SecretEncryptionPort
from app.modules.connectors.infrastructure.models import Account


class AccountRepository(
    SqlAlchemyRepository[Account, AccountEntity],
    AccountRepositoryPort,
):
    """Repository for Account operations."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        encryption: SecretEncryptionPort,
        message_bus: MessageBus | None = None,
    ):
        super().__init__(uow, Account, AccountEntity)
        self.encryption = encryption
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    @staticmethod
    def _serialize_credentials(credentials: object | None) -> dict | None:
        if credentials is None:
            return None
        model_dump = getattr(credentials, "model_dump", None)
        if callable(model_dump):
            return model_dump(mode="json")
        if isinstance(credentials, dict):
            return credentials
        return None

    async def _to_model(self, entity: AccountEntity) -> Account:
        data = entity.model_dump(exclude_unset=True)
        data["credentials"] = await self.encryption.encrypt_json_async(
            self._serialize_credentials(entity.credentials)
        )
        return self.model_cls(**data)

    def _reraise_as_conflict_if_duplicate_identity(
        self, exc: IntegrityError, connector_id: str
    ) -> None:
        """Translate a uq_accounts_provider_identity violation into a clean
        409, rather than letting the raw IntegrityError propagate.

        App-level dedup (``_reject_if_identity_already_connected`` in
        ConnectorService) already rejects the common case before either
        create/update runs, but that check-then-act has a TOCTOU gap under
        concurrency (e.g. two near-simultaneous OAuth callbacks for the same
        identity) -- this is the backstop at the DB boundary. Re-raises
        unrelated IntegrityErrors untouched (the same table also has a
        uq_accounts_default_per_auth_config constraint).
        """
        if "uq_accounts_provider_identity" in str(exc.orig):
            raise AccountAlreadyConnectedError(connector_id) from exc
        raise exc

    async def _to_entity(self, instance: Account) -> AccountEntity:
        credentials = await self.encryption.decrypt_json_async(instance.credentials)
        data = {
            "id": instance.id,
            "user_id": instance.user_id,
            "organization_id": instance.organization_id,
            "auth_config_id": instance.auth_config_id,
            "connector_id": instance.connector_id,
            "is_default": instance.is_default,
            "status": instance.status,
            "provider_account_id": instance.provider_account_id,
            "email": instance.email,
            "display_name": instance.display_name,
            "credentials": credentials,
            "preferences": instance.preferences,
            "allowed_scopes": instance.allowed_scopes,
            "created_at": instance.created_at,
            "updated_at": instance.updated_at,
        }
        if instance.connector is not None:
            data["connector"] = instance.connector.to_entity()
        return AccountEntity.model_validate(data)

    async def create(self, entity: AccountEntity) -> AccountEntity:
        """Create new account with eager loaded connector."""
        instance = await self._to_model(entity)
        self.session.add(instance)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            self._reraise_as_conflict_if_duplicate_identity(exc, entity.connector_id)
        await self.session.refresh(instance, attribute_names=["connector"])
        return await self._to_entity(instance)

    async def update(self, entity: AccountEntity) -> AccountEntity:
        """Update account with eager loaded connector."""
        stmt = (
            select(Account)
            .where(Account.id == entity.id)
            .options(selectinload(Account.connector))
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()

        if not instance:
            raise AccountNotFoundError(str(entity.id))

        instance.credentials = await self.encryption.encrypt_json_async(
            self._serialize_credentials(entity.credentials)
        )
        instance.provider_account_id = entity.provider_account_id
        instance.email = entity.email
        instance.display_name = entity.display_name
        instance.status = entity.status.value if hasattr(entity.status, "value") else str(entity.status)

        try:
            await self.session.flush()
        except IntegrityError as exc:
            self._reraise_as_conflict_if_duplicate_identity(exc, entity.connector_id)
        return await self._to_entity(instance)

    async def get(self, id: UUID) -> Optional[AccountEntity]:
        """Get account by ID with connector."""
        stmt = (
            select(Account)
            .where(Account.id == id)
            .options(selectinload(Account.connector))
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return await self._to_entity(instance) if instance else None

    async def get_by_user_and_app(
        self, user_id: UUID, connector_id: str
    ) -> Optional[AccountEntity]:
        """Get the user's default (or oldest) account for a connector."""
        stmt = (
            select(Account)
            .where(Account.user_id == user_id, Account.connector_id == connector_id)
            .order_by(Account.is_default.desc(), Account.created_at)
            .options(selectinload(Account.connector))
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return await self._to_entity(instance) if instance else None

    async def get_by_user_org_and_app(
        self, user_id: UUID, organization_id: UUID, connector_id: str
    ) -> Optional[AccountEntity]:
        """Org-scoped counterpart of :meth:`get_by_user_and_app` — the user's
        default (or oldest) account for a connector within one organization."""
        stmt = (
            select(Account)
            .where(
                Account.user_id == user_id,
                Account.organization_id == organization_id,
                Account.connector_id == connector_id,
            )
            .order_by(Account.is_default.desc(), Account.created_at)
            .options(selectinload(Account.connector))
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return await self._to_entity(instance) if instance else None

    async def get_by_user_and_auth_config(
        self, user_id: UUID, auth_config_id: UUID
    ) -> Optional[AccountEntity]:
        """Get the user's default (or oldest) account for an auth config."""
        stmt = (
            select(Account)
            .where(Account.user_id == user_id, Account.auth_config_id == auth_config_id)
            .order_by(Account.is_default.desc(), Account.created_at)
            .options(selectinload(Account.connector))
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return await self._to_entity(instance) if instance else None

    async def get_by_user_org_and_auth_config(
        self, user_id: UUID, organization_id: UUID, auth_config_id: UUID
    ) -> Optional[AccountEntity]:
        """Org-scoped counterpart of :meth:`get_by_user_and_auth_config`."""
        stmt = (
            select(Account)
            .where(
                Account.user_id == user_id,
                Account.organization_id == organization_id,
                Account.auth_config_id == auth_config_id,
            )
            .order_by(Account.is_default.desc(), Account.created_at)
            .options(selectinload(Account.connector))
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return await self._to_entity(instance) if instance else None

    async def get_by_user_auth_config_and_provider_account(
        self,
        user_id: UUID,
        auth_config_id: UUID,
        provider_account_id: str,
    ) -> Optional[AccountEntity]:
        """Get a specific account by its provider-side identity.

        Used on OAuth re-auth to update the right account when a user has
        connected more than one identity for the same auth config.
        """
        stmt = (
            select(Account)
            .where(
                Account.user_id == user_id,
                Account.auth_config_id == auth_config_id,
                Account.provider_account_id == provider_account_id,
            )
            .options(selectinload(Account.connector))
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return await self._to_entity(instance) if instance else None

    async def promote_next_default(
        self,
        user_id: UUID,
        auth_config_id: UUID,
        exclude_account_id: UUID,
    ) -> Optional[AccountEntity]:
        """Make the user's oldest remaining account (excluding one being deleted)
        the default for this auth config, so the "exactly one default" invariant
        holds after the current default is removed. No-op when none remain."""
        stmt = (
            select(Account)
            .where(
                Account.user_id == user_id,
                Account.auth_config_id == auth_config_id,
                Account.id != exclude_account_id,
            )
            .order_by(Account.is_default.desc(), Account.created_at)
            .limit(1)
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        if instance is None:
            return None
        if not instance.is_default:
            instance.is_default = True
            await self.session.flush()
        return await self._to_entity(instance)

    async def list_by_auth_config(
        self,
        auth_config_id: UUID,
    ) -> Sequence[AccountEntity]:
        stmt = (
            select(Account)
            .where(Account.auth_config_id == auth_config_id)
            .options(selectinload(Account.connector))
        )
        result = await self.session.execute(stmt)
        return [await self._to_entity(instance) for instance in result.scalars().all()]

    async def list_by_user(
        self,
        user_id: UUID,
        limit: int = 100,
        cursor: UUID | None = None,
    ) -> tuple[Sequence[AccountEntity], UUID | None]:
        """List accounts by user using UUID cursor pagination."""
        stmt = (
            select(Account)
            .where(Account.user_id == user_id)
            .options(selectinload(Account.connector))
        )
        if cursor is not None:
            stmt = stmt.where(Account.id > cursor)
        stmt = stmt.order_by(Account.id).limit(limit + 1)
        result = await self.session.execute(stmt)
        instances = list(result.scalars().all())

        next_cursor = None
        if len(instances) > limit:
            next_cursor = instances[limit - 1].id
            instances = instances[:limit]

        return [await self._to_entity(instance) for instance in instances], next_cursor

    async def list_by_user_and_org(
        self,
        user_id: UUID,
        organization_id: UUID,
        connector_id: str | None = None,
        limit: int = 100,
        cursor: UUID | None = None,
    ) -> tuple[Sequence[AccountEntity], UUID | None]:
        stmt = (
            select(Account)
            .where(Account.user_id == user_id, Account.organization_id == organization_id)
            .options(selectinload(Account.connector))
        )
        if connector_id:
            stmt = stmt.where(Account.connector_id == connector_id)
        if cursor is not None:
            stmt = stmt.where(Account.id > cursor)
        stmt = stmt.order_by(Account.id).limit(limit + 1)
        result = await self.session.execute(stmt)
        instances = list(result.scalars().all())

        next_cursor = None
        if len(instances) > limit:
            next_cursor = instances[limit - 1].id
            instances = instances[:limit]

        return [await self._to_entity(instance) for instance in instances], next_cursor
