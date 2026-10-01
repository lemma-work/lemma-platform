from collections.abc import Sequence
from datetime import timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.repository import SqlAlchemyRepository
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.events import ConnectorConnectedEvent
from app.modules.connectors.domain.account import AccountEntity, AccountStatus
from app.modules.connectors.domain.errors import (
    AccountAlreadyConnectedError,
    AccountNotFoundError,
)
from app.modules.connectors.domain.ports import AccountRepositoryPort
from app.modules.connectors.infrastructure.models import Account, AuthConfig
from app.modules.connectors.infrastructure.repositories.account_credential_store import (
    AccountCredentialStore,
)
from app.modules.connectors.infrastructure.repositories.connector_secrets import (
    ACCOUNT_CREDENTIALS_PURPOSE,
    ACCOUNTS_TABLE,
    account_scope,
    credentials_expiry,
    serialize_credentials,
)
from app.modules.vault.contracts import (
    LeaseToken,
    Revealed,
    SecretMeta,
    SecretRef,
    Vault,
    vault_for,
)


class AccountRepository(
    SqlAlchemyRepository[Account, AccountEntity],
    AccountRepositoryPort,
):
    """Repository for Account operations.

    The credentials are not a column. They are one vault secret per account
    (see ``connector_secrets``), revealed on every read that returns an entity
    and written only through :meth:`create` and :meth:`replace_credentials`.
    :meth:`update` deliberately leaves them alone -- see there for why.
    """

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        message_bus: MessageBus | None = None,
        vault: Vault | None = None,
    ):
        super().__init__(uow, Account, AccountEntity)
        self._vault = vault or vault_for(uow)
        self._credentials = AccountCredentialStore(self.session, self._vault)
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

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
        """Entities for a page of rows, with ONE vault read for all of them.

        Revealing per row would be a query per account on every list -- the N+1
        a page of accounts is exactly large enough to notice.
        """
        expected = {
            instance.credentials_secret_id: account_scope(
                instance.organization_id, instance.user_id
            )
            for instance in instances
            if instance.credentials_secret_id is not None
        }
        revealed = await self._vault.reveal_many(
            expected, purpose=ACCOUNT_CREDENTIALS_PURPOSE
        )
        return [
            self._entity_from(
                instance,
                revealed.get(instance.credentials_secret_id)
                if instance.credentials_secret_id is not None
                else None,
            )
            for instance in instances
        ]

    @staticmethod
    def _entity_from(instance: Account, secret: Revealed | None) -> AccountEntity:
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
            "credentials": secret.json() if secret is not None else None,
            # What a caller compares against when it writes the credentials
            # back, so a write based on a stale read is refused, not applied.
            "credentials_version": secret.version if secret is not None else None,
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
        value = serialize_credentials(entity.credentials)
        if value is not None:
            ref = await self._vault.put(
                scope=account_scope(entity.organization_id, entity.user_id),
                purpose=ACCOUNT_CREDENTIALS_PURPOSE,
                value=value,
                owner_table=ACCOUNTS_TABLE,
                expires_at=credentials_expiry(value),
            )
            instance.credentials_secret_id = ref.id
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
        caller that had read an account a credential writer. Two of them --
        binding a GitHub installation, and the reconciler that does it on read
        -- re-wrote credentials they had read *before* a refresh, putting a
        spent single-use GitHub refresh token back over the one the refresh had
        just stored; the next refresh then failed and the account fell to
        REAUTH_REQUIRED for no reason the person could see. Credentials change
        only through :meth:`replace_credentials`, which can refuse a stale
        write.
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

    # ------------------------------------------------------------ credentials
    async def replace_credentials(
        self,
        account_id: UUID,
        credentials: object | None,
        *,
        expected_version: int | None = None,
        lease: LeaseToken | None = None,
    ) -> SecretRef | None:
        """Store new credentials for an account, keeping the account.

        ``expected_version`` is the ``credentials_version`` the caller read: a
        write based on an older read raises ``SecretVersionConflict`` rather
        than overwriting a newer credential (a rotated refresh token, most
        often, which is single-use). ``lease`` is the refresh lease the caller
        took; writing ends it.

        An account with no secret yet gets one. ``None`` removes the
        credentials; the vault row goes with the column (a trigger deletes it).
        """
        return await self._credentials.replace_account_secret(
            account_id, credentials, expected_version=expected_version, lease=lease
        )

    async def set_status(self, account_id: UUID, status: AccountStatus) -> bool:
        """Move an account to ``status`` and nothing else.

        A targeted write rather than :meth:`update`, which rewrites every
        descriptive column from an entity the caller may have read a while ago.
        """
        result = await self.session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(status=AccountStatus(status).value)
        )
        return bool(result.rowcount)

    async def set_external_ref(self, account_id: UUID, external_ref: str) -> bool:
        """Point an account's inbound routing key at ``external_ref``, nothing else.

        The GitHub bind paths used :meth:`update`, from an entity read before
        they refreshed its token -- which rewrote the status the refresh had
        just set back to the one they read.
        """
        result = await self.session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(external_ref=external_ref)
        )
        return bool(result.rowcount)

    async def try_lease_credentials(
        self, account_id: UUID, *, if_version: int, holder: str, ttl: timedelta
    ) -> LeaseToken | None:
        """Take the right to refresh this account's credentials, or ``None``.

        Granted to exactly one caller, and only while the credentials are still
        at ``if_version`` -- a caller that read them before somebody else's
        refresh is told no, and should read again instead of refreshing a token
        that is already spent. The caller commits so the others see it.
        """
        return await self._credentials.lease_account_secret(
            account_id, if_version=if_version, holder=holder, ttl=ttl
        )

    async def release_credentials_lease(self, lease: LeaseToken) -> None:
        """Give the refresh lease back without writing (the refresh failed)."""
        await self._credentials.release_account_lease(lease)

    async def credentials_meta(self, account_id: UUID) -> SecretMeta | None:
        """Version, expiry and lease of the credentials, without decrypting."""
        return await self._credentials.account_secret_meta(account_id)

    async def connector_and_kind(self, account_id: UUID) -> tuple[str, str] | None:
        """``(connector_id, install kind)`` for an account, from columns alone.

        Answering it through :meth:`get` decrypted the account's credentials and
        the install's config to read two plaintext columns.
        """
        row = (
            await self.session.execute(
                select(Account.connector_id, AuthConfig.kind)
                .join(AuthConfig, AuthConfig.id == Account.auth_config_id)
                .where(Account.id == account_id)
            )
        ).first()
        if row is None:
            return None
        return row.connector_id, row.kind
