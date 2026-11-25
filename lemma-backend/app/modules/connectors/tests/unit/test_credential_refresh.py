"""Refreshing an account's token once, however many callers need it at once.

Providers that rotate refresh tokens with reuse detection revoke the whole token
family when a spent one is used again. So two callers finding the same expired
token must produce ONE provider call, and the second must end up with the first
one's result.

The account repository here is an in-memory stand-in whose credentials live in
the shared ``FakeVault`` -- so the lease, the version check and the refusals
under test are the vault contract's, not a mock's say-so. Collaborators are
injected through constructors; nothing is patched.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, create_autospec
from uuid import UUID, uuid4

import pytest

from app.modules.connectors.domain.account import (
    AccountEntity,
    AccountStatus,
    OAuthCredentials,
)
from app.modules.connectors.domain.errors import (
    ConnectorReauthRequiredError,
    OAuthWorkflowError,
)
from app.modules.connectors.infrastructure.repositories.connector_secrets import (
    ACCOUNT_CREDENTIALS_PURPOSE,
    account_scope,
    serialize_credentials,
)
from app.modules.connectors.services.auth.auth_provider import AuthProviderInterface
from app.modules.connectors.services.credential_refresh import (
    CredentialRefresher,
    RefreshRequest,
)
from app.modules.test_support.vault_fake import FakeSecret, FakeVault

pytestmark = pytest.mark.asyncio


class _Uow:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _VaultBackedAccounts:
    """The credential half of ``AccountRepository``, over a ``FakeVault``.

    Mirrors the real repository: the entity's credentials and version come
    from the vault, and writes go through the vault's compare-and-set.
    """

    def __init__(self, vault: FakeVault, account: AccountEntity) -> None:
        self.vault = vault
        self.scope = account_scope(account.organization_id, account.user_id)
        self.row = account.model_copy()
        self.secret_id = _seed(vault, self.scope, account.credentials)
        self.stored = 0

    async def get(self, account_id: UUID) -> AccountEntity | None:
        if account_id != self.row.id:
            return None
        revealed = await self.vault.reveal(
            self.secret_id, expect=self.scope, purpose=ACCOUNT_CREDENTIALS_PURPOSE
        )
        return self.row.model_copy(
            update={
                "credentials": OAuthCredentials.model_validate(revealed.json()),
                "credentials_version": revealed.version,
            }
        )

    async def store_refreshed(
        self, account_id, credentials, *, expected_version=None, lease=None
    ) -> None:
        self.stored += 1
        await self.vault.replace(
            self.secret_id,
            expect=self.scope,
            purpose=ACCOUNT_CREDENTIALS_PURPOSE,
            value=serialize_credentials(credentials),
            expected_version=expected_version,
            lease=lease,
        )
        self.row.status = AccountStatus.CONNECTED

    async def stored_credentials(self, account_id):
        return self.vault.value_of(self.secret_id)

    async def set_status(self, account_id, status) -> None:
        self.row.status = status

    async def try_lease_credentials(self, account_id, *, if_version, holder, ttl):
        return await self.vault.try_lease(
            self.secret_id,
            expect=self.scope,
            purpose=ACCOUNT_CREDENTIALS_PURPOSE,
            holder=holder,
            ttl=ttl,
            if_version=if_version,
        )

    async def release_credentials_lease(self, lease) -> None:
        await self.vault.release_lease(lease)

    async def credentials_meta(self, account_id):
        return await self.vault.meta(
            self.secret_id, expect=self.scope, purpose=ACCOUNT_CREDENTIALS_PURPOSE
        )


def _seed(vault: FakeVault, scope, credentials) -> UUID:
    """The row's existing secret, at version 1."""
    secret_id = uuid4()
    vault.secrets[secret_id] = FakeSecret(
        scope=scope,
        purpose=ACCOUNT_CREDENTIALS_PURPOSE,
        owner_table="accounts",
        value=serialize_credentials(credentials),
        version=1,
        expires_at=None,
        updated_at=datetime.now(timezone.utc),
    )
    return secret_id


def _expired() -> OAuthCredentials:
    return OAuthCredentials(
        access_token="old",
        refresh_token="single-use",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )


def _refreshed() -> OAuthCredentials:
    return OAuthCredentials(
        access_token="new",
        refresh_token="rotated",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def _accounts(vault: FakeVault) -> _VaultBackedAccounts:
    return _VaultBackedAccounts(
        vault,
        AccountEntity(
            id=uuid4(),
            user_id=uuid4(),
            organization_id=uuid4(),
            auth_config_id=uuid4(),
            connector_id="github",
            credentials=_expired(),
        ),
    )


def _provider(result: OAuthCredentials | Exception, *, delay: float = 0.05):
    provider = create_autospec(AuthProviderInterface, instance=True)

    async def refresh(**_kwargs):
        await asyncio.sleep(delay)
        if isinstance(result, Exception):
            raise result
        return result

    provider.refresh_credentials.side_effect = refresh
    return provider


def _request(entity: AccountEntity, provider) -> RefreshRequest:
    return RefreshRequest(
        account=entity,
        auth_provider=provider,
        install=Mock(),
        current=entity.credentials,
        is_expired=True,
    )


async def test_two_callers_on_one_expired_token_make_one_provider_call():
    vault = FakeVault()
    accounts = _accounts(vault)
    provider = _provider(_refreshed())
    first_read = await accounts.get(accounts.row.id)
    second_read = await accounts.get(accounts.row.id)

    first, second = await asyncio.gather(
        CredentialRefresher(accounts, _Uow(), poll_interval=0.01).refresh(
            _request(first_read, provider)
        ),
        CredentialRefresher(accounts, _Uow(), poll_interval=0.01).refresh(
            _request(second_read, provider)
        ),
    )

    assert provider.refresh_credentials.await_count == 1
    assert accounts.stored == 1
    assert first.access_token == second.access_token == "new"
    assert vault.value_of(accounts.secret_id)["refresh_token"] == "rotated"
    # The write ended the lease; nothing is left held.
    assert vault.secrets[accounts.secret_id].lease_holder is None


async def test_a_refresh_based_on_a_stale_read_uses_the_newer_credentials():
    """Somebody refreshed after our read: the lease is refused at our version,
    and we return what they stored instead of spending the token again."""
    vault = FakeVault()
    accounts = _accounts(vault)
    stale = await accounts.get(accounts.row.id)
    await accounts.store_refreshed(accounts.row.id, _refreshed(), expected_version=1)
    provider = _provider(OAuthCredentials(access_token="never"))

    result = await CredentialRefresher(accounts, _Uow(), poll_interval=0.01).refresh(
        _request(stale, provider)
    )

    provider.refresh_credentials.assert_not_awaited()
    assert result.access_token == "new"


async def test_a_withdrawn_grant_gives_the_lease_back_and_marks_reauth():
    vault = FakeVault()
    accounts = _accounts(vault)
    entity = await accounts.get(accounts.row.id)
    provider = _provider(ConnectorReauthRequiredError(reason="invalid_grant"), delay=0)

    with pytest.raises(ConnectorReauthRequiredError):
        await CredentialRefresher(accounts, _Uow()).refresh(_request(entity, provider))

    assert vault.secrets[accounts.secret_id].lease_holder is None
    assert accounts.row.status == AccountStatus.REAUTH_REQUIRED
    assert vault.secrets[accounts.secret_id].version == 1


async def test_a_provider_outage_gives_the_lease_back_and_marks_nothing():
    """A 5xx says nothing about the grant: the expired token fails this call,
    and the next use tries again."""
    vault = FakeVault()
    accounts = _accounts(vault)
    entity = await accounts.get(accounts.row.id)
    provider = _provider(RuntimeError("502 from the token endpoint"), delay=0)

    with pytest.raises(OAuthWorkflowError):
        await CredentialRefresher(accounts, _Uow()).refresh(_request(entity, provider))

    assert vault.secrets[accounts.secret_id].lease_holder is None
    assert accounts.row.status == AccountStatus.CONNECTED


async def test_a_waiter_does_not_wait_forever_on_a_stuck_holder():
    """A holder that never writes (a crashed worker) must not strand the rest:
    the waiter gives up after its budget and says the refresh failed."""
    vault = FakeVault()
    accounts = _accounts(vault)
    entity = await accounts.get(accounts.row.id)
    await accounts.try_lease_credentials(
        entity.id, if_version=1, holder="stuck", ttl=timedelta(minutes=5)
    )
    provider = _provider(_refreshed())
    refresher = CredentialRefresher(
        accounts, _Uow(), poll_interval=0.01, wait_seconds=0.05
    )

    with pytest.raises(OAuthWorkflowError):
        await refresher.refresh(_request(entity, provider))

    provider.refresh_credentials.assert_not_awaited()


async def test_a_holder_whose_lease_ran_out_uses_the_newer_write():
    """Our lease expired mid-call and somebody else refreshed: our write is
    refused by the version check, and theirs is what we hand back."""
    vault = FakeVault()
    accounts = _accounts(vault)
    entity = await accounts.get(accounts.row.id)
    provider = create_autospec(AuthProviderInterface, instance=True)

    async def refresh(**_kwargs):
        # Somebody else's refresh lands while ours is out.
        await accounts.store_refreshed(
            entity.id, OAuthCredentials(access_token="theirs", refresh_token="t")
        )
        return OAuthCredentials(access_token="ours", refresh_token="o")

    provider.refresh_credentials.side_effect = refresh
    uow = _Uow()

    result = await CredentialRefresher(accounts, uow).refresh(
        _request(entity, provider)
    )

    assert result.access_token == "theirs"
    assert uow.rollbacks == 1
    assert vault.value_of(accounts.secret_id)["access_token"] == "theirs"
