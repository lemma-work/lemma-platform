"""An account's credentials in the vault, addressed by account id.

The half of ``AccountRepository`` that writes credentials, leases them for a
refresh, and reads their version -- everything that works on the secret rather
than on the account row. It is apart so the repository stays readable (and
under the file-size ceiling), not so it can be used on its own: the repository
is still the one door, and it delegates here.

Every method resolves the secret and its scope from the account row, never
from anything the caller hands over, because the scope is bound into the
ciphertext.
"""

from __future__ import annotations

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
    LeaseToken,
    SecretMeta,
    SecretRef,
    SecretScope,
    SecretVersionConflict,
    Vault,
)


class AccountCredentialStore:
    def __init__(self, session: AsyncSession, vault: Vault) -> None:
        self._session = session
        self._vault = vault

    async def _locate_account_secret(
        self, account_id: UUID
    ) -> tuple[UUID | None, SecretScope]:
        """The account's secret id (``None`` if it has none) and its scope.

        Columns only: nothing here needs the credentials decrypted.
        """
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

    async def replace_account_secret(
        self,
        account_id: UUID,
        credentials: object | None,
        *,
        expected_version: int | None,
        lease: LeaseToken | None,
    ) -> SecretRef | None:
        """See ``AccountRepository.replace_credentials``."""
        secret_id, scope = await self._locate_account_secret(account_id)
        value = serialize_credentials(credentials)
        if value is None:
            if secret_id is not None:
                # The trigger deletes the vault row with the pointer.
                await self._session.execute(
                    update(Account)
                    .where(Account.id == account_id)
                    .values(credentials_secret_id=None)
                )
            return None
        if secret_id is not None:
            return await self._vault.replace(
                secret_id,
                expect=scope,
                purpose=ACCOUNT_CREDENTIALS_PURPOSE,
                value=value,
                expected_version=expected_version,
                lease=lease,
                expires_at=credentials_expiry(value),
            )
        ref = await self._vault.put(
            scope=scope,
            purpose=ACCOUNT_CREDENTIALS_PURPOSE,
            value=value,
            owner_table=ACCOUNTS_TABLE,
            expires_at=credentials_expiry(value),
        )
        # Conditional on the column still being empty: two writers creating the
        # first secret at once must not both win, one silently orphaning the
        # other's credential.
        result = await self._session.execute(
            update(Account)
            .where(Account.id == account_id, Account.credentials_secret_id.is_(None))
            .values(credentials_secret_id=ref.id)
        )
        if not result.rowcount:
            raise SecretVersionConflict(ref.id, expected_version or 0, ref.version)
        return ref

    async def lease_account_secret(
        self, account_id: UUID, *, if_version: int, holder: str, ttl: timedelta
    ) -> LeaseToken | None:
        secret_id, scope = await self._locate_account_secret(account_id)
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

    async def release_account_lease(self, lease: LeaseToken) -> None:
        await self._vault.release_lease(lease)

    async def account_secret_meta(self, account_id: UUID) -> SecretMeta | None:
        secret_id, scope = await self._locate_account_secret(account_id)
        if secret_id is None:
            return None
        return await self._vault.meta(
            secret_id, expect=scope, purpose=ACCOUNT_CREDENTIALS_PURPOSE
        )
