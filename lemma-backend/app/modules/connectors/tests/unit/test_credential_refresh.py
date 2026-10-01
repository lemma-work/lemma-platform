"""Refreshing an account's token once, however many callers need it at once.

Providers that rotate refresh tokens treat reuse of a spent one as theft and
revoke the grant. So two callers finding the same expired token must produce
ONE provider call, and the second must end up with the first one's result.

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
from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigSource,
)
from app.modules.connectors.domain.connector import (
    ConnectorEntity,
    ConnectorKind,
    HttpKindSpec,
)
from app.modules.connectors.domain.errors import OAuthWorkflowError
from app.modules.connectors.infrastructure.repositories.connector_secrets import (
    ACCOUNT_CREDENTIALS_PURPOSE,
    account_scope,
    serialize_credentials,
)
from app.modules.connectors.services.auth.auth_provider import AuthProviderInterface
from app.modules.connectors.services.connector_service import ConnectorService
from app.modules.connectors.services.credential_refresh import (
    CredentialRefresher,
    RefreshRequest,
)
from app.modules.test_support.vault_fake import FakeSecret, FakeVault

pytestmark = pytest.mark.asyncio

ORG_ID = uuid4()


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
        self.secret_id = vault_put(vault, self.scope, account.credentials)
        self.replaced = 0

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

    async def replace_credentials(
        self, account_id, credentials, *, expected_version=None, lease=None
    ):
        self.replaced += 1
        return await self.vault.replace(
            self.secret_id,
            expect=self.scope,
            purpose=ACCOUNT_CREDENTIALS_PURPOSE,
            value=serialize_credentials(credentials),
            expected_version=expected_version,
            lease=lease,
        )

    async def set_status(self, account_id, status) -> bool:
        self.row.status = status
        return True

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


def vault_put(vault: FakeVault, scope, credentials) -> UUID:
    """Seed the vault synchronously, as the row's existing secret (version 1)."""
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


def _account(credentials: OAuthCredentials) -> AccountEntity:
    return AccountEntity(
        id=uuid4(),
        user_id=uuid4(),
        organization_id=ORG_ID,
        auth_config_id=uuid4(),
        connector_id="github",
        credentials=credentials,
    )


def _slow_provider(result: OAuthCredentials | Exception, *, delay: float = 0.05):
    provider = create_autospec(AuthProviderInterface, instance=True)

    async def refresh(**_kwargs):
        await asyncio.sleep(delay)
        if isinstance(result, Exception):
            raise result
        return result

    provider.refresh_credentials.side_effect = refresh
    return provider


def _service(accounts: _VaultBackedAccounts, provider, uow: _Uow) -> ConnectorService:
    auth_config = AuthConfigEntity(
        id=accounts.row.auth_config_id,
        organization_id=ORG_ID,
        connector_id="github",
        kind=ConnectorKind.HTTP,
        config_source=AuthConfigSource.SYSTEM_DEFAULT,
        name="github",
    )
    connectors = Mock()

    async def get_connector(_connector_id):
        return ConnectorEntity(id="github", kinds=[HttpKindSpec()])

    async def get_auth_config(_auth_config_id):
        return auth_config

    connectors.get.side_effect = get_connector
    auth_configs = Mock()
    auth_configs.get.side_effect = get_auth_config
    access = Mock()

    async def yes(**_kwargs):
        return True

    access.organization_exists.side_effect = yes
    access.user_has_organization_role.side_effect = yes
    return ConnectorService(
        uow=uow,
        connector_repository=connectors,
        auth_config_repository=auth_configs,
        account_repository=accounts,
        connect_request_repository=Mock(),
        auth_provider_registry=Mock(get=Mock(return_value=provider)),
        redirect_uri_builder=Mock(),
        organization_access=access,
        system_oauth_config=Mock(
            has_default_oauth_config=Mock(return_value=True),
            get_default_oauth_config=Mock(return_value=None),
            resolve_oauth2_defaults=Mock(return_value=None),
        ),
    )


def _request(accounts: _VaultBackedAccounts, entity: AccountEntity, provider):
    return RefreshRequest(
        account=entity,
        auth_provider=provider,
        install=Mock(),
        current=entity.credentials,
        is_expired=True,
    )


async def test_two_callers_on_one_expired_token_make_one_provider_call():
    vault = FakeVault()
    accounts = _VaultBackedAccounts(vault, _account(_expired()))
    provider = _slow_provider(_refreshed())
    service = _service(accounts, provider, _Uow())
    account = accounts.row

    first, second = await asyncio.gather(
        service.get_account_credentials(account.id, account.user_id),
        service.get_account_credentials(account.id, account.user_id),
    )

    assert provider.refresh_credentials.await_count == 1
    assert accounts.replaced == 1
    assert first.access_token == second.access_token == "new"
    assert vault.value_of(accounts.secret_id)["refresh_token"] == "rotated"
    # The write ended the lease; nothing is left held.
    assert vault.secrets[accounts.secret_id].lease_holder is None


async def test_a_refresh_based_on_a_stale_read_uses_the_newer_credentials():
    """Somebody refreshed after our read: the lease is refused at our version,
    and we return what they stored instead of spending the token again."""
    vault = FakeVault()
    accounts = _VaultBackedAccounts(vault, _account(_expired()))
    stale = await accounts.get(accounts.row.id)
    await accounts.replace_credentials(
        accounts.row.id, _refreshed(), expected_version=1
    )
    provider = _slow_provider(OAuthCredentials(access_token="never"))
    refresher = CredentialRefresher(accounts, _Uow(), poll_interval=0.01)

    result = await refresher.refresh(_request(accounts, stale, provider))

    provider.refresh_credentials.assert_not_awaited()
    assert result.access_token == "new"


async def test_a_failed_refresh_gives_the_lease_back_and_marks_reauth():
    vault = FakeVault()
    accounts = _VaultBackedAccounts(vault, _account(_expired()))
    entity = await accounts.get(accounts.row.id)
    provider = _slow_provider(RuntimeError("token revoked"), delay=0)
    uow = _Uow()

    with pytest.raises(OAuthWorkflowError):
        await CredentialRefresher(accounts, uow).refresh(
            _request(accounts, entity, provider)
        )

    assert vault.secrets[accounts.secret_id].lease_holder is None
    assert accounts.row.status == AccountStatus.REAUTH_REQUIRED
    assert vault.secrets[accounts.secret_id].version == 1


async def test_a_waiter_does_not_wait_forever_on_a_stuck_holder():
    """A holder that never writes (a crashed worker) must not strand the rest:
    the waiter gives up after its budget and says the refresh failed."""
    vault = FakeVault()
    accounts = _VaultBackedAccounts(vault, _account(_expired()))
    entity = await accounts.get(accounts.row.id)
    await accounts.try_lease_credentials(
        entity.id, if_version=1, holder="stuck", ttl=timedelta(minutes=5)
    )
    provider = _slow_provider(_refreshed())
    refresher = CredentialRefresher(
        accounts, _Uow(), poll_interval=0.01, wait_seconds=0.05
    )

    with pytest.raises(OAuthWorkflowError):
        await refresher.refresh(_request(accounts, entity, provider))

    provider.refresh_credentials.assert_not_awaited()
