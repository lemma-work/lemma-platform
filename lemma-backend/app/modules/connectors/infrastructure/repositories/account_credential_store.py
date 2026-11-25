"""An account's credentials in the vault, addressed by account id.

The half of ``AccountRepository`` that works on the secret rather than on the
account row: revealing a page of credentials, writing them, leasing them for a
refresh and reading their version. It is apart so the repository stays
readable, not so it can be used on its own -- the repository is still the one
door, and it delegates here.

Every method resolves the secret and its scope from the account row, never
from anything the caller hands over, because the scope is bound into the
ciphertext.

Rollout: between migration ``0078_vault_cutover`` and the next release's
contract migration, a row written by a process on the previous release holds
its credentials in the old Fernet column with no ``credentials_secret_id``.
Those are read from there (``legacy_json``) and move into the vault the next
time they are written. Every write here sets the old column to NULL, so an old
process reading the row fails closed rather than replaying a stale token.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.connectors.domain.errors import AccountNotFoundError
from app.modules.connectors.infrastructure.models import Account
from app.modules.connectors.infrastructure.repositories.connector_secrets import (
    ACCOUNT_CREDENTIALS_PURPOSE,
    ACCOUNTS_TABLE,
    account_scope,
    credentials_expiry,
    serialize_credentials,
)
from app.modules.vault.contracts import (
    JsonObject,
    LeaseToken,
    SecretMeta,
    SecretScope,
    SecretVersionConflict,
    Vault,
    legacy_json,
)


@dataclass(frozen=True, slots=True)
class StoredCredentials:
    """Credentials as read, and the vault version they were read at."""

    value: JsonObject | None
    #: ``None`` when they are not in the vault (none, or old-column only).
    version: int | None


class AccountCredentialStore:
    def __init__(self, session: AsyncSession, vault: Vault) -> None:
        self._session = session
        self._vault = vault

    # ------------------------------------------------------------------ read
    async def read_many(
        self, instances: Sequence[Account]
    ) -> dict[UUID, StoredCredentials]:
        """Every row's credentials by account id, with ONE vault read.

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
        out: dict[UUID, StoredCredentials] = {}
        for instance in instances:
            secret_id = instance.credentials_secret_id
            if secret_id is not None:
                secret = revealed.get(secret_id)
                out[instance.id] = (
                    StoredCredentials(secret.json(), secret.version)
                    if secret is not None
                    else StoredCredentials(None, None)
                )
            else:
                out[instance.id] = StoredCredentials(
                    await legacy_json(instance.credentials), None
                )
        return out

    async def read_locked(self, account_id: UUID) -> JsonObject | None:
        """The credentials the row holds now, locking the row until the
        transaction ends. Columns only, so nothing a session already holds can
        answer in the row's place."""
        row = (
            await self._session.execute(
                select(
                    Account.credentials_secret_id,
                    Account.organization_id,
                    Account.user_id,
                    Account.credentials,
                )
                .where(Account.id == account_id)
                .with_for_update()
            )
        ).first()
        if row is None:
            return None
        if row.credentials_secret_id is None:
            return await legacy_json(row.credentials)
        secret = await self._vault.reveal(
            row.credentials_secret_id,
            expect=account_scope(row.organization_id, row.user_id),
            purpose=ACCOUNT_CREDENTIALS_PURPOSE,
        )
        return secret.json()

    async def _locate(self, account_id: UUID) -> tuple[UUID | None, SecretScope]:
        """The account's secret id (``None`` if it has none) and its scope."""
        row = (
            await self._session.execute(
                select(
                    Account.credentials_secret_id,
                    Account.organization_id,
                    Account.user_id,
                ).where(Account.id == account_id)
            )
        ).first()
        if row is None:
            raise AccountNotFoundError(str(account_id))
        return row.credentials_secret_id, account_scope(
            row.organization_id, row.user_id
        )

    # ----------------------------------------------------------------- write
    async def put_new(
        self, organization_id: UUID, user_id: UUID, credentials: object | None
    ) -> UUID | None:
        """A secret for a row about to be inserted; its id, or ``None`` for no
        credentials."""
        value = serialize_credentials(credentials)
        if value is None:
            return None
        ref = await self._vault.put(
            scope=account_scope(organization_id, user_id),
            purpose=ACCOUNT_CREDENTIALS_PURPOSE,
            value=value,
            owner_table=ACCOUNTS_TABLE,
            expires_at=credentials_expiry(value),
        )
        return ref.id

    async def write(
        self,
        account_id: UUID,
        credentials: object | None,
        *,
        expected_version: int | None = None,
        lease: LeaseToken | None = None,
        also: Mapping[str, object] | None = None,
    ) -> None:
        """Store ``credentials`` for an existing account, with the columns in
        ``also`` written in the same statement.

        ``expected_version``/``lease`` are passed to the vault's compare-and-set
        (``SecretVersionConflict`` when the stored version moved). An account
        with no secret yet gets one -- conditionally on the column still being
        empty, so two writers creating the first secret at once do not both
        win. ``None`` removes the credentials; the release trigger deletes the
        vault row with the pointer.
        """
        secret_id, scope = await self._locate(account_id)
        value = serialize_credentials(credentials)
        values: dict[str, object] = {"credentials": None, **(also or {})}
        stmt = update(Account).where(Account.id == account_id)
        if value is None:
            values["credentials_secret_id"] = None
        elif secret_id is not None:
            await self._vault.replace(
                secret_id,
                expect=scope,
                purpose=ACCOUNT_CREDENTIALS_PURPOSE,
                value=value,
                expected_version=expected_version,
                lease=lease,
                expires_at=credentials_expiry(value),
            )
        else:
            ref = await self._vault.put(
                scope=scope,
                purpose=ACCOUNT_CREDENTIALS_PURPOSE,
                value=value,
                owner_table=ACCOUNTS_TABLE,
                expires_at=credentials_expiry(value),
            )
            values["credentials_secret_id"] = ref.id
            stmt = stmt.where(Account.credentials_secret_id.is_(None))
        result = await self._session.execute(
            stmt.values(**values).returning(Account.id)
        )
        if result.scalar_one_or_none() is None:
            if secret_id is None and value is not None:
                # Someone else gave the account its first secret meanwhile.
                raise SecretVersionConflict(account_id, expected_version or 0, 1)
            raise AccountNotFoundError(str(account_id))

    # ----------------------------------------------------------------- lease
    async def try_lease(
        self, account_id: UUID, *, if_version: int, holder: str, ttl: timedelta
    ) -> LeaseToken | None:
        secret_id, scope = await self._locate(account_id)
        if secret_id is None:
            return None
        return await self._vault.try_lease(
            secret_id,
            expect=scope,
            purpose=ACCOUNT_CREDENTIALS_PURPOSE,
            holder=holder,
            ttl=ttl,
            if_version=if_version,
        )

    async def release_lease(self, lease: LeaseToken) -> None:
        await self._vault.release_lease(lease)

    async def meta(self, account_id: UUID) -> SecretMeta | None:
        secret_id, scope = await self._locate(account_id)
        if secret_id is None:
            return None
        return await self._vault.meta(
            secret_id, expect=scope, purpose=ACCOUNT_CREDENTIALS_PURPOSE
        )
