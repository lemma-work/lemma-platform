from datetime import timedelta
from typing import Optional, Sequence
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.infrastructure.db.repository import SqlAlchemyRepository
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.events import ConnectorConnectedEvent
from app.modules.connectors.domain.account import AccountEntity, AccountStatus
from app.modules.connectors.domain.errors import (
    AccountAlreadyConnectedError,
    AccountNotFoundError,
)
from app.modules.connectors.domain.ports import AccountRepositoryPort
from app.modules.connectors.infrastructure.models import Account
from app.modules.connectors.infrastructure.repositories.account_credential_store import (
    AccountCredentialStore,
    StoredCredentials,
)
from app.modules.vault.contracts import LeaseToken, SecretMeta, Vault, vault_for


class AccountRepository(
    SqlAlchemyRepository[Account, AccountEntity],
    AccountRepositoryPort,
):
    """Repository for Account operations.

    The credentials are one vault secret per account (``connector_secrets``),
    revealed on every read that returns an entity and written only through
    :meth:`create`, :meth:`replace_credentials` and :meth:`store_refreshed`.
    :meth:`update` deliberately leaves them alone -- see there for why.
    """

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        vault: Vault | None = None,
    ):
        super().__init__(uow, Account, AccountEntity)
        self._credentials = AccountCredentialStore(
            self.session, vault or vault_for(self.session)
        )

    def _to_model(self, entity: AccountEntity) -> Account:
        # ``connector`` is a read-side domain projection, not a relationship
        # assignment for writes. Passing an explicit ``connector=None`` to the
        # ORM model makes SQLAlchemy synchronize the relationship by clearing
        # ``connector_id``, even when the entity contains a valid connector id.
        # The credentials go to the vault, and their version is read-side only.
        data = entity.model_dump(
            exclude_unset=True,
            exclude={"connector", "credentials", "credentials_version"},
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
        return (await self._to_entities([instance]))[0]

    async def _to_entities(self, instances: Sequence[Account]) -> list[AccountEntity]:
        """Entities for a page of rows, with one vault read for all of them."""
        stored = await self._credentials.read_many(instances)
        return [
            self._entity_from(instance, stored[instance.id]) for instance in instances
        ]

    @staticmethod
    def _entity_from(instance: Account, stored: StoredCredentials) -> AccountEntity:
        data = {
            "id": instance.id,
            "user_id": instance.user_id,
            "organization_id": instance.organization_id,
            "auth_config_id": instance.auth_config_id,
            "connector_id": instance.connector_id,
            "is_default": instance.is_default,
            "status": instance.status,
            "provider_account_id": instance.provider_account_id,
            "external_ref": instance.external_ref,
            "email": instance.email,
            "display_name": instance.display_name,
            "credentials": stored.value,
            "credentials_version": stored.version,
            "preferences": instance.preferences,
            "allowed_scopes": instance.allowed_scopes,
            "created_at": instance.created_at,
            "updated_at": instance.updated_at,
        }
        if instance.connector is not None:
            data["connector"] = instance.connector.to_entity()
        return AccountEntity.model_validate(data)

    async def create(self, entity: AccountEntity) -> AccountEntity:
        """Create new account with eager loaded connector.

        The secret is written first, in the same transaction, so the row can
        reference it; a failed insert rolls both back together.
        """
        instance = self._to_model(entity)
        instance.credentials_secret_id = await self._credentials.put_new(
            entity.organization_id, entity.user_id, entity.credentials
        )
        self.session.add(instance)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            self._reraise_as_conflict_if_duplicate_identity(exc, entity.connector_id)
        await self.session.refresh(instance, attribute_names=["connector"])
        # The single write path behind both connect routes -- the OAuth callback
        # and the direct create. No `provider`: it lives on the auth config, not
        # on the account, and a property that could only ever be empty is worse
        # than one that is not declared.
        self.uow.collect_events(
            [
                ConnectorConnectedEvent(
                    connector_id=instance.connector_id,
                    organization_id=instance.organization_id,
                    user_id=instance.user_id,
                )
            ]
        )
        return await self._to_entity(instance)

    async def update(self, entity: AccountEntity) -> AccountEntity:
        """Update the account's descriptive columns. NOT its credentials.

        It used to write ``entity.credentials`` back too, which made every
        caller that had read an account a credential writer -- and an entity
        is a copy made when it was read. A token refreshed since, by this
        request or another, is not on it, so writing it back put a spent
        single-use refresh token (GitHub's among them) over the live one, and
        the account fell to REAUTH_REQUIRED for no reason anyone could see.
        Credentials change only through :meth:`replace_credentials` and
        :meth:`store_refreshed`.
        """
        stmt = (
            select(Account)
            .where(Account.id == entity.id)
            .options(selectinload(Account.connector))
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()

        if not instance:
            raise AccountNotFoundError(str(entity.id))

        instance.provider_account_id = entity.provider_account_id
        # Written here because re-auth changes it. Someone reconnecting Slack
        # into a different workspace keeps the old `team.id` otherwise, and
        # inbound events for the workspace they left keep routing to this
        # account -- the cross-tenant leak the column exists to prevent. The
        # caller already sets it on the entity for exactly this reason.
        instance.external_ref = entity.external_ref
        instance.email = entity.email
        instance.display_name = entity.display_name
        instance.status = (
            entity.status.value
            if hasattr(entity.status, "value")
            else str(entity.status)
        )

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
            # Eager, like every other select in this file. Without it
            # `_to_entity` touches `instance.connector` after the greenlet that
            # could have loaded it is gone, and SQLAlchemy raises
            # `MissingGreenlet` -- so deleting an account that happened to be
            # the default answered 500 and left the "exactly one default"
            # invariant unrepaired. This was the one select that omitted it.
            .options(selectinload(Account.connector))
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

    async def mark_connected_for_reauth(self, auth_config_id: UUID) -> int:
        """Flag every CONNECTED account on this install, and say how many.

        One statement rather than a read of every account -- credentials and
        `selectinload`ed connector included -- followed by an `update()` per row
        that reads it back again. The read existed to filter on `status`, which
        is a column the `WHERE` can filter on.

        Deliberately still not a delete and not a credential wipe: the rows keep
        their ids, grants and stored credentials, so anything referencing them
        still resolves and the reconnect updates in place.
        """
        result = await self.session.execute(
            update(Account)
            .where(
                Account.auth_config_id == auth_config_id,
                Account.status == AccountStatus.CONNECTED.value,
            )
            .values(status=AccountStatus.REAUTH_REQUIRED.value)
        )
        return int(result.rowcount or 0)

    # Narrow statements, because `update` writes every descriptive column from
    # the entity it is handed, and an entity is a copy made when it was read: a
    # status a refresh set since is not on it. (It once wrote the credentials
    # too, which put spent single-use refresh tokens back; it no longer does.)

    async def set_status(self, account_id: UUID, status: AccountStatus) -> None:
        """Write the status and nothing else."""
        await self.session.execute(
            update(Account).where(Account.id == account_id).values(status=status.value)
        )

    async def set_external_ref(
        self, account_id: UUID, external_ref: str | None
    ) -> None:
        """Write the inbound routing key and nothing else."""
        await self.session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(external_ref=external_ref)
        )

    async def store_refreshed(
        self,
        account_id: UUID,
        credentials: object,
        *,
        expected_version: int | None = None,
        lease: LeaseToken | None = None,
    ) -> None:
        """Write a refreshed credential, and that the account is connected.

        ``expected_version`` is the version the refresh started from and
        ``lease`` the refresh lease it holds: a write whose base has moved
        raises ``SecretVersionConflict`` rather than overwriting the newer
        credential, and the write ends the lease.

        The status is written every time, not only when this session thinks it
        changed. A refresh that lost a race may have marked the account for a
        reconnect a moment ago. The ORM writes a column only when it differs
        from the copy the session holds, and a copy read before the mark says
        the status never moved -- so the mark could be left standing, and an
        install that names its target does not retry a marked account
        (``reauth_recovery``).
        """
        await self._credentials.write(
            account_id,
            credentials,
            expected_version=expected_version,
            lease=lease,
            also={"status": AccountStatus.CONNECTED.value},
        )

    async def replace_credentials(
        self, account_id: UUID, credentials: object | None
    ) -> None:
        """Store credentials outright, keeping the account.

        For a new grant, a credential the person supplied, or a token the
        platform just minted -- never a copy read earlier, which is why there
        is no version check. ``None`` removes them.
        """
        await self._credentials.write(account_id, credentials)

    async def try_lease_credentials(
        self, account_id: UUID, *, if_version: int, holder: str, ttl: timedelta
    ) -> LeaseToken | None:
        """The refresh lease, if nobody holds it and the stored credentials are
        still at ``if_version``."""
        return await self._credentials.try_lease(
            account_id, if_version=if_version, holder=holder, ttl=ttl
        )

    async def release_credentials_lease(self, lease: LeaseToken) -> None:
        await self._credentials.release_lease(lease)

    async def credentials_meta(self, account_id: UUID) -> SecretMeta | None:
        """Version and lease of the stored credentials, without decrypting."""
        return await self._credentials.meta(account_id)

    async def stored_credentials(self, account_id: UUID) -> dict[str, object] | None:
        """The credentials the row holds now, read past this session's copy.

        The row is locked until the transaction ends, so what is decided from
        the answer cannot be overtaken: a refresh storing a new credential
        either committed before this read and is in it, or writes after
        whatever the caller does next.

        A column select, not an entity one. While the session still holds the
        instance it loaded, an entity read answers with that instance and does
        not overwrite it, so `get` here could answer with what this request saw
        when it started -- exactly what a caller asking this wants to look
        behind. Whether it still holds one is up to the garbage collector; a
        column always comes from the row.
        """
        return await self._credentials.read_locked(account_id)

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
        return await self._to_entities(list(result.scalars().all()))

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

        return await self._to_entities(instances), next_cursor

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
            .where(
                Account.user_id == user_id, Account.organization_id == organization_id
            )
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

        return await self._to_entities(instances), next_cursor
