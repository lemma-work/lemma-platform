"""An account marked for reauth is tried again when trying risks nothing.

The mark was terminal: resolution refused the account before any call, and only
a reconnect cleared it -- so an account marked by mistake (a 403 on one
operation, a token endpoint that timed out once) was refused forever, credential
still good. These pin which marked accounts are now let through to be verified,
and which are still refused before the call because trying would send the
credential somewhere it was never issued for.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

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
from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.domain.pod_connector import (
    PodAccountEntity,
    PodConnectorEntity,
    PodConnectorMode,
)
from app.modules.connectors.domain.pod_connector_errors import ReauthRequiredError
from app.modules.connectors.services import install_update
from app.modules.connectors.services.pod_account_rule import (
    AccountAsk,
    PodAccountPicker,
)
from app.modules.connectors.services.reauth_recovery import (
    TARGET_FIELDS,
    clear_reauth_mark,
    reauth_blocks_use,
    reauth_may_recover,
)

pytestmark = pytest.mark.unit

ORG_ID = uuid4()


def _install(
    kind: ConnectorKind = ConnectorKind.COMPOSIO, config: dict | None = None
) -> AuthConfigEntity:
    return AuthConfigEntity(
        id=uuid4(),
        organization_id=ORG_ID,
        connector_id="googlebigquery",
        kind=kind,
        config_source=AuthConfigSource.SYSTEM_DEFAULT,
        name="bigquery",
        config=config,
    )


def _account(
    install: AuthConfigEntity,
    credentials,
    status: AccountStatus = AccountStatus.REAUTH_REQUIRED,
) -> AccountEntity:
    return AccountEntity(
        id=uuid4(),
        user_id=uuid4(),
        organization_id=ORG_ID,
        auth_config_id=install.id,
        connector_id=install.connector_id,
        status=status,
        credentials=credentials,
    )


class TestWhichMarkedAccountsAreTriedAgain:
    def test_a_composio_connection_is_verified_by_a_refresh(self):
        # The BigQuery account production found stuck: Composio says whether
        # the connection is alive, so asking it risks nothing.
        install = _install()
        account = _account(
            install, OAuthCredentials(access_token="t", connection_id="ca_1")
        )
        assert reauth_may_recover(account, install)
        assert not reauth_blocks_use(account, install)

    def test_an_oauth_grant_with_a_refresh_token_is_verified_by_a_refresh(self):
        install = _install(ConnectorKind.HTTP)
        account = _account(
            install, OAuthCredentials(access_token="t", refresh_token="r")
        )
        assert not reauth_blocks_use(account, install)

    def test_a_plain_secret_on_an_install_with_no_target_is_tried(self):
        install = _install(ConnectorKind.HTTP)
        account = _account(install, {"api_key": "k"})
        assert not reauth_blocks_use(account, install)

    def test_an_unrenewable_oauth_token_is_still_refused(self):
        # It may belong to an OAuth app the admin has since replaced, and the
        # provider would accept it anyway; only a refresh could tell, and there
        # is nothing to refresh with.
        install = _install(ConnectorKind.HTTP)
        account = _account(install, OAuthCredentials(access_token="t"))
        assert reauth_blocks_use(account, install)

    @pytest.mark.parametrize(
        ("kind", "config"),
        [
            (ConnectorKind.MCP, {"server_url": "https://mcp.example.com"}),
            (ConnectorKind.HTTP, {"server_url": "https://api.example.com"}),
            (ConnectorKind.SQL, {"host": "db.example.com", "database": "crm"}),
        ],
        ids=["mcp", "http", "sql"],
    )
    def test_an_install_that_names_its_target_is_never_retried(self, kind, config):
        # Moving the target is the deliberate reason install_update marks
        # accounts; retrying would hand the credential to the new system.
        install = _install(kind, config)
        renewable = _account(
            install, OAuthCredentials(access_token="t", refresh_token="r")
        )
        secret = _account(install, {"api_key": "k"})
        assert reauth_blocks_use(renewable, install)
        assert reauth_blocks_use(secret, install)

    def test_an_account_with_no_credential_is_refused(self):
        install = _install()
        assert reauth_blocks_use(_account(install, None), install)

    def test_an_unmarked_account_is_never_blocked(self):
        install = _install(ConnectorKind.SQL, {"host": "db"})
        account = _account(
            install, OAuthCredentials(access_token="t"), AccountStatus.CONNECTED
        )
        assert not reauth_blocks_use(account, install)
        assert not reauth_may_recover(account, install)

    def test_the_targets_are_the_ones_install_update_marks_on(self):
        # One table, read by both: the fields whose change marks accounts are
        # exactly the fields whose presence stops a retry.
        assert install_update.TARGET_FIELDS is TARGET_FIELDS


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (AccountStatus.REAUTH_REQUIRED, AccountStatus.CONNECTED),
        # Disconnected on purpose; a call that happened to work does not undo it.
        (AccountStatus.DISCONNECTED, AccountStatus.DISCONNECTED),
        (AccountStatus.CONNECTED, AccountStatus.CONNECTED),
    ],
)
async def test_clearing_the_mark_only_ever_moves_a_reauth_account(before, after):
    install = _install()
    account = _account(install, {"api_key": "k"}, before)
    accounts = AsyncMock()
    accounts.get.return_value = account
    uow = AsyncMock()

    await clear_reauth_mark(accounts, uow, account.id)

    assert account.status is after
    if before is after:
        accounts.update.assert_not_awaited()
    else:
        accounts.update.assert_awaited_once_with(account)
        uow.commit.assert_awaited_once()


class TestResolution:
    """Refused before the call, or handed on to be verified."""

    @staticmethod
    def _picker(account: AccountEntity, owner: object):
        pod_id = uuid4()
        pod_account = PodAccountEntity(
            id=uuid4(),
            pod_id=pod_id,
            account_id=account.id,
            auth_config_id=account.auth_config_id,
            owner_user_id=owner,
            is_member_default=True,
        )
        pod_accounts = AsyncMock()
        pod_accounts.get_member_default.return_value = pod_account
        accounts = AsyncMock()
        accounts.get.return_value = account
        picker = PodAccountPicker(
            pod_accounts=pod_accounts,
            accounts=accounts,
            is_pod_member=AsyncMock(return_value=True),
        )
        connector = PodConnectorEntity(
            id=uuid4(),
            pod_id=pod_id,
            slug="bigquery",
            auth_config_id=account.auth_config_id,
            mode=PodConnectorMode.PER_MEMBER,
        )
        return picker, connector

    @staticmethod
    def _ask(person) -> AccountAsk:
        return AccountAsk(person=person, shared_check=AsyncMock(), record_use=False)

    async def test_a_recoverable_account_is_handed_on_to_be_verified(self):
        install = _install()
        account = _account(
            install, OAuthCredentials(access_token="t", connection_id="ca_1")
        )
        picker, connector = self._picker(account, account.user_id)

        resolved = await picker.pick(connector, install, self._ask(account.user_id))

        assert resolved.account.id == account.id
        # Still marked: only the provider accepting it may clear the mark.
        assert resolved.account.status is AccountStatus.REAUTH_REQUIRED

    async def test_an_account_that_cannot_be_verified_is_refused_before_the_call(
        self,
    ):
        install = _install(ConnectorKind.MCP, {"server_url": "https://mcp.example"})
        account = _account(
            install, OAuthCredentials(access_token="t", refresh_token="r")
        )
        picker, connector = self._picker(account, account.user_id)

        with pytest.raises(ReauthRequiredError) as raised:
            await picker.pick(connector, install, self._ask(account.user_id))

        assert raised.value.code == "REAUTH_REQUIRED"
        assert raised.value.status_code == 409
