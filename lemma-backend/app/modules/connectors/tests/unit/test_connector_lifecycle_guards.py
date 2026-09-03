"""Invariants of the connect/repair lifecycle -- the paths a person drives.

Creating an install, connecting an account, completing an OAuth callback,
disconnecting. Each of them calls a provider whose latency is somebody else's,
at a rate set by somebody else too, on a request-scoped session; each of them
addresses an install by id; and the page they all start from is one API call.
Three properties follow, none of which had a test:

* **A session is never held across a provider call.** `docs/development.md`
  states the rule -- resolve in a short unit of work, commit, then do the slow
  thing -- and `ConnectorOperationUseCases` is its worked example on the
  execution path. The property is an ordering, so these tests record one: every
  commit and every provider call land in a single list, and the assertion is
  that a commit comes first. Asserting a commit *count* would pass just as well
  with the commit in the wrong place, which is the only thing that could
  regress.
* **A disabled install is off everywhere**, including the id-addressed paths
  these use -- and disconnecting from one still works, or an admin could not
  undo a compromised install without deleting it and its accounts with it.
* **The landing page asks one question per thing it needs**, not one per row.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, create_autospec, patch
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
    AuthConfigStatus,
)
from app.modules.connectors.domain.connect_request import (
    ConnectRequestEntity,
    ConnectRequestStatus,
)
from app.modules.connectors.domain.connector import (
    AuthScheme,
    ConnectorEntity,
    ConnectorKind,
    LemmaProviderCapability,
    McpKindSpec,
)
from app.modules.connectors.domain.errors import (
    ConnectorValidationError,
    OAuthWorkflowError,
)
from app.modules.connectors.services.auth.auth_provider import AuthProviderInterface
from app.modules.connectors.services.connector_service import ConnectorService

pytestmark = pytest.mark.asyncio

ORG_ID = uuid4()


class _Journal:
    """What happened, in the order it happened."""

    def __init__(self):
        self.entries: list[str] = []

    def record(self, name: str):
        def _note(*_args, **_kwargs):
            self.entries.append(name)

        return _note

    def released_before(self, name: str) -> bool:
        if name not in self.entries:
            raise AssertionError(f"{name} never happened; journal={self.entries}")
        return "commit" in self.entries[: self.entries.index(name)]


def _uow(journal: _Journal) -> AsyncMock:
    uow = AsyncMock()
    uow.commit.side_effect = journal.record("commit")
    return uow


def _connector(connector_id: str = "slack", **kwargs) -> ConnectorEntity:
    return ConnectorEntity(
        id=connector_id,
        provider_capabilities=[LemmaProviderCapability(**kwargs)],
    )


def _mcp_connector() -> ConnectorEntity:
    return ConnectorEntity(id="mcp", provider_capabilities=[McpKindSpec()])


def _auth_config(connector_id: str = "slack") -> AuthConfigEntity:
    return AuthConfigEntity(
        id=uuid4(),
        organization_id=ORG_ID,
        connector_id=connector_id,
        provider="LEMMA",
        config_source=AuthConfigSource.SYSTEM_DEFAULT,
        name=connector_id,
    )


def _service(**overrides) -> ConnectorService:
    deps = {
        "uow": AsyncMock(),
        "connector_repository": AsyncMock(get=AsyncMock(return_value=_connector())),
        "auth_config_repository": AsyncMock(),
        "account_repository": AsyncMock(),
        "connect_request_repository": AsyncMock(),
        "auth_provider_registry": Mock(),
        "redirect_uri_builder": Mock(build=Mock(return_value="https://cb")),
        "organization_access": AsyncMock(
            organization_exists=AsyncMock(return_value=True),
            user_has_organization_role=AsyncMock(return_value=True),
        ),
        "system_oauth_config": Mock(
            has_default_oauth_config=Mock(return_value=True),
            get_default_oauth_config=Mock(return_value=None),
            resolve_oauth2_defaults=Mock(return_value=None),
        ),
    }
    deps.update(overrides)
    return ConnectorService(**deps)


def _auth_provider(journal: _Journal, method: str, result=None):
    provider = create_autospec(AuthProviderInterface, instance=True)
    call = getattr(provider, method)
    call.side_effect = journal.record(method)
    call.return_value = result
    return provider


async def test_the_oauth_callback_releases_before_the_token_exchange():
    journal = _Journal()
    user_id = uuid4()
    auth_config = _auth_config()
    connect_request = ConnectRequestEntity(
        id=uuid4(),
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        connector_id="slack",
        authorization_url="https://auth",
        status=ConnectRequestStatus.PENDING,
        attributes={"state": "s", "code_verifier": "v", "provider_state": "p"},
    )
    provider = _auth_provider(
        journal,
        "exchange_code_for_credentials",
        OAuthCredentials(access_token="xoxb"),
    )
    account_repo = AsyncMock()
    account_repo.get_by_user_and_auth_config.return_value = None
    account_repo.create.side_effect = lambda entity: entity
    connect_repo = AsyncMock()
    connect_repo.claim_pending_by_state.return_value = connect_request
    connect_repo.update.side_effect = lambda req: req

    service = _service(
        uow=_uow(journal),
        auth_config_repository=AsyncMock(
            get=AsyncMock(return_value=auth_config),
        ),
        account_repository=account_repo,
        connect_request_repository=connect_repo,
        auth_provider_registry=Mock(get=Mock(return_value=provider)),
    )

    with patch.object(
        service, "_load_native_account_profile", AsyncMock(return_value=None)
    ):
        await service.handle_oauth_callback(
            redirect_uri="https://cb?state=s&code=abc", state="s"
        )

    # The claim is spent by the same commit, which is what makes single-use
    # survive a domain error raised out of the exchange.
    assert journal.released_before("exchange_code_for_credentials")


async def test_a_failed_exchange_scrubs_the_secrets_it_spent():
    """The success path already dropped these. The error path is the one
    nobody comes back to, so its row keeps them for good."""
    journal = _Journal()
    user_id = uuid4()
    auth_config = _auth_config()
    connect_request = ConnectRequestEntity(
        id=uuid4(),
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        connector_id="slack",
        authorization_url="https://auth",
        status=ConnectRequestStatus.PENDING,
        attributes={"state": "s", "code_verifier": "v", "provider_state": "p"},
    )
    provider = create_autospec(AuthProviderInterface, instance=True)
    provider.exchange_code_for_credentials.side_effect = RuntimeError("refused")
    connect_repo = AsyncMock()
    connect_repo.claim_pending_by_state.return_value = connect_request
    connect_repo.update.side_effect = lambda req: req

    service = _service(
        uow=_uow(journal),
        auth_config_repository=AsyncMock(get=AsyncMock(return_value=auth_config)),
        connect_request_repository=connect_repo,
        auth_provider_registry=Mock(get=Mock(return_value=provider)),
    )

    with pytest.raises(OAuthWorkflowError):
        await service.handle_oauth_callback(
            redirect_uri="https://cb?state=s&code=abc", state="s"
        )

    stored = connect_repo.update.await_args.args[0]
    assert stored.status is ConnectRequestStatus.ERROR
    assert stored.attributes == {"state": "s"}


async def test_creating_a_credential_account_releases_before_the_provider():
    journal = _Journal()
    auth_config = _auth_config("notion")
    provider = _auth_provider(
        journal,
        "connect_with_credentials",
        OAuthCredentials(access_token="secret"),
    )
    account_repo = AsyncMock()
    account_repo.get_by_user_and_auth_config.return_value = None
    account_repo.get_by_user_auth_config_and_provider_account.return_value = None
    account_repo.create.side_effect = lambda entity: entity

    service = _service(
        uow=_uow(journal),
        connector_repository=AsyncMock(
            get=AsyncMock(
                return_value=_connector("notion", auth_scheme=AuthScheme.API_KEY)
            )
        ),
        auth_config_repository=AsyncMock(get=AsyncMock(return_value=auth_config)),
        account_repository=account_repo,
        auth_provider_registry=Mock(get=Mock(return_value=provider)),
    )

    await service.create_account(
        user_id=uuid4(),
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        credentials={"api_key": "k"},
    )

    assert journal.released_before("connect_with_credentials")


async def test_deleting_an_account_releases_before_the_revoke():
    journal = _Journal()
    user_id = uuid4()
    auth_config = _auth_config()
    account = AccountEntity(
        id=uuid4(),
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        connector_id="slack",
        status=AccountStatus.CONNECTED,
        credentials=OAuthCredentials(access_token="xoxb"),
    )
    provider = _auth_provider(journal, "revoke_connection")

    service = _service(
        uow=_uow(journal),
        connector_repository=AsyncMock(
            get=AsyncMock(
                return_value=_connector("slack", auth_scheme=AuthScheme.OAUTH2)
            )
        ),
        auth_config_repository=AsyncMock(get=AsyncMock(return_value=auth_config)),
        account_repository=AsyncMock(get=AsyncMock(return_value=account)),
        auth_provider_registry=Mock(get=Mock(return_value=provider)),
    )

    await service.delete_account(account.id, user_id, ORG_ID)

    assert journal.released_before("revoke_connection")


async def test_creating_an_install_releases_before_validating_its_target():
    """Install validation resolves DNS through the SSRF guard and MCP
    negotiation is up to three round trips to a server the tenant names."""
    journal = _Journal()
    auth_config_repo = AsyncMock(get_active_by_org_and_app=AsyncMock(return_value=None))
    auth_config_repo.create.side_effect = lambda entity: entity

    service = _service(
        uow=_uow(journal),
        connector_repository=AsyncMock(get=AsyncMock(return_value=_mcp_connector())),
        auth_config_repository=auth_config_repo,
    )

    async def _validate(**kwargs):
        journal.entries.append("validate_install_config")
        return kwargs["config"]

    with patch(
        "app.modules.connectors.services.connector_service.validate_install_config",
        _validate,
    ):
        await service.create_auth_config(
            user_id=uuid4(),
            organization_id=ORG_ID,
            connector_id="mcp",
            kind=ConnectorKind.MCP.value,
            config_source=AuthConfigSource.ORG_CUSTOM.value,
            config={"server_url": "https://mcp.example.com/mcp"},
        )

    assert journal.released_before("validate_install_config")


class TestADisabledInstallIsActuallyOff:
    """`status: DISABLED` is the only way short of deletion to stop an install
    being used, and deletion cascades away every account on it.

    Two of the three resolution branches filtered on ACTIVE in SQL. The third
    -- addressing the install by id -- did not, and that is the branch the
    connect-request, account-create and execution paths all take.
    """

    @staticmethod
    def _disabled() -> AuthConfigEntity:
        install = _auth_config()
        install.status = AuthConfigStatus.DISABLED
        return install

    async def _service_for(self, install: AuthConfigEntity) -> ConnectorService:
        return _service(
            auth_config_repository=AsyncMock(get=AsyncMock(return_value=install)),
            auth_provider_registry=Mock(
                get=Mock(return_value=create_autospec(AuthProviderInterface))
            ),
        )

    async def test_a_connect_request_against_it_is_refused(self):
        install = self._disabled()
        service = await self._service_for(install)

        with pytest.raises(ConnectorValidationError) as refusal:
            await service.initiate_connect_request(
                user_id=uuid4(), organization_id=ORG_ID, auth_config_id=install.id
            )

        # Named, not a 404: the caller supplied an id for an install they can
        # already see, so "not found" would send them looking for a row that is
        # right there.
        assert refusal.value.details == {"reason": "install_disabled"}
        assert install.name in refusal.value.message

    async def test_connecting_an_account_to_it_is_refused(self):
        install = self._disabled()
        service = await self._service_for(install)

        with pytest.raises(ConnectorValidationError):
            await service.create_account(
                user_id=uuid4(),
                organization_id=ORG_ID,
                auth_config_id=install.id,
                credentials={"api_key": "k"},
            )

    async def test_deleting_an_account_on_it_still_works(self):
        """The one thing that has to keep working while an install is off:
        disconnecting from it."""
        install = self._disabled()
        user_id = uuid4()
        account = AccountEntity(
            id=uuid4(),
            user_id=user_id,
            organization_id=ORG_ID,
            auth_config_id=install.id,
            connector_id="slack",
            status=AccountStatus.CONNECTED,
            credentials=OAuthCredentials(access_token="xoxb"),
        )
        account_repo = AsyncMock(get=AsyncMock(return_value=account))
        service = _service(
            auth_config_repository=AsyncMock(get=AsyncMock(return_value=install)),
            account_repository=account_repo,
            auth_provider_registry=Mock(
                get=Mock(return_value=create_autospec(AuthProviderInterface))
            ),
        )

        await service.delete_account(account.id, user_id, ORG_ID)

        account_repo.delete.assert_awaited_once_with(account.id)


async def test_the_status_page_reads_every_title_in_one_query():
    """One `WHERE id IN (...)`, not one `get()` per distinct connector. This is
    the connectors landing page's single call."""
    connector_repo = AsyncMock(
        titles_for=AsyncMock(return_value={"slack": "Slack", "gmail": "Gmail"})
    )
    service = _service(
        connector_repository=connector_repo,
        auth_config_repository=AsyncMock(
            list_by_org=AsyncMock(return_value=([_auth_config("slack")], None))
        ),
        account_repository=AsyncMock(
            list_by_user_and_org=AsyncMock(return_value=([], None))
        ),
    )

    status = await service.get_connector_status(user_id=uuid4(), organization_id=ORG_ID)

    assert status["installed"][0]["title"] == "Slack"
    connector_repo.titles_for.assert_awaited_once_with(["slack"])
    connector_repo.get.assert_not_awaited()
