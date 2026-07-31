from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from app.modules.connectors.domain.account import (
    AccountEntity,
    AccountStatus,
    ComposioCredentials,
    OAuthCredentials,
)
from app.modules.connectors.domain.connector import (
    AuthScheme,
    ConnectorEntity,
    AuthProvider,
    ComposioProviderCapability,
    LemmaProviderCapability,
)
from app.modules.connectors.domain.connector_operation import (
    ConnectorOperationEntity,
)
from app.modules.connectors.domain.auth_config import AuthConfigEntity, AuthConfigSource
from app.modules.connectors.domain.connect_request import ConnectRequestEntity
from app.modules.connectors.domain.connect_request import ConnectRequestStatus
from app.modules.connectors.domain.errors import (
    AccountAlreadyConnectedError,
    ConnectorNotFoundError,
    ConnectorValidationError,
    ConnectRequestStateRequiredError,
    OAuthWorkflowError,
)
from app.modules.connectors.services.connector_service import ConnectorService

pytestmark = pytest.mark.asyncio

ORG_ID = uuid4()


def _connector(id: str = "slack") -> ConnectorEntity:
    return ConnectorEntity(id=id, provider_capabilities=[LemmaProviderCapability()])


def _auth_config(connector_id: str = "slack") -> AuthConfigEntity:
    return AuthConfigEntity(
        id=uuid4(),
        organization_id=ORG_ID,
        connector_id=connector_id,
        provider="LEMMA",
        config_source=AuthConfigSource.SYSTEM_DEFAULT,
        name=connector_id,
    )


def _account(user_id, connector_id: str = "slack") -> AccountEntity:
    auth_config = _auth_config(connector_id)
    return AccountEntity(
        id=uuid4(),
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        connector_id=connector_id,
        credentials=OAuthCredentials(access_token="token"),
    )


def _org_access() -> AsyncMock:
    return AsyncMock(
        organization_exists=AsyncMock(return_value=True),
        user_has_organization_role=AsyncMock(return_value=True),
    )


def _system_oauth() -> Mock:
    return Mock(
        has_default_oauth_config=Mock(return_value=True),
        get_default_oauth_config=Mock(return_value=None),
        resolve_oauth2_defaults=Mock(return_value=None),
    )


def _auth_config_repo(auth_config: AuthConfigEntity | None = None) -> AsyncMock:
    auth_config = auth_config or _auth_config()
    return AsyncMock(
        get=AsyncMock(return_value=auth_config),
        get_active_by_org_and_app=AsyncMock(return_value=auth_config),
        get_active_by_org_and_name=AsyncMock(return_value=auth_config),
    )


def _service(**overrides) -> ConnectorService:
    deps = {
        "uow": AsyncMock(),
        "connector_repository": AsyncMock(get=AsyncMock(return_value=_connector())),
        "auth_config_repository": _auth_config_repo(),
        "account_repository": AsyncMock(),
        "connect_request_repository": AsyncMock(),
        "auth_provider_registry": AsyncMock(),
        "redirect_uri_builder": Mock(),
        "organization_access": _org_access(),
        "system_oauth_config": _system_oauth(),
    }
    deps.update(overrides)
    return ConnectorService(**deps)


async def test_get_connector_raises_not_found():
    service = _service(
        connector_repository=AsyncMock(get=AsyncMock(return_value=None)),
    )

    with pytest.raises(ConnectorNotFoundError):
        await service.get_connector("missing")


async def test_initiate_connect_request_allowed_when_account_exists():
    """Multiple accounts per auth config are allowed, so an existing connected
    account no longer blocks a new connect request."""
    user_id = uuid4()
    auth_config = _auth_config()
    auth_provider = AsyncMock()
    auth_provider.get_authorization_url.return_value = ("https://auth", "provider_state")
    registry = Mock()
    registry.get.return_value = auth_provider
    uow = AsyncMock()
    connect_repo = AsyncMock()
    connect_repo.create.side_effect = lambda req: req
    redirect_builder = Mock()
    redirect_builder.build.return_value = "https://callback"

    service = _service(
        uow=uow,
        connector_repository=AsyncMock(get=AsyncMock(return_value=_connector())),
        auth_config_repository=_auth_config_repo(auth_config),
        account_repository=AsyncMock(
            get_by_user_and_auth_config=AsyncMock(return_value=_account(user_id))
        ),
        connect_request_repository=connect_repo,
        auth_provider_registry=registry,
        redirect_uri_builder=redirect_builder,
    )

    result = await service.initiate_connect_request(
        user_id=user_id, organization_id=ORG_ID, connector_id="slack"
    )

    assert isinstance(result, ConnectRequestEntity)


async def test_initiate_connect_request_success():
    user_id = uuid4()
    auth_provider = AsyncMock()
    auth_provider.get_authorization_url.return_value = ("https://auth", "provider_state")
    registry = Mock()
    registry.get.return_value = auth_provider
    uow = AsyncMock()
    connect_repo = AsyncMock()
    connect_repo.create.side_effect = lambda req: req
    redirect_builder = Mock()
    redirect_builder.build.return_value = "https://callback"

    service = _service(
        uow=uow,
        connector_repository=AsyncMock(get=AsyncMock(return_value=_connector())),
        account_repository=AsyncMock(
            get_by_user_and_auth_config=AsyncMock(return_value=None)
        ),
        connect_request_repository=connect_repo,
        auth_provider_registry=registry,
        redirect_uri_builder=redirect_builder,
    )

    result = await service.initiate_connect_request(
        user_id=user_id, organization_id=ORG_ID, connector_id="slack"
    )

    assert isinstance(result, ConnectRequestEntity)
    assert result.user_id == user_id
    assert result.connector_id == "slack"
    uow.commit.assert_awaited_once()


async def test_create_composio_auth_config_allows_system_default_without_env_key():
    user_id = uuid4()
    app = ConnectorEntity(
        id="dropbox",
        provider_capabilities=[
            ComposioProviderCapability(toolkit_slug="dropbox")
        ],
    )
    auth_config_repo = AsyncMock(
        get_active_by_org_and_app=AsyncMock(return_value=None),
    )
    auth_config_repo.create.side_effect = lambda entity: entity
    uow = AsyncMock()
    service = _service(
        uow=uow,
        connector_repository=AsyncMock(get=AsyncMock(return_value=app)),
        auth_config_repository=auth_config_repo,
    )

    result = await service.create_auth_config(
        user_id=user_id,
        organization_id=ORG_ID,
        connector_id="dropbox",
        provider=AuthProvider.COMPOSIO.value,
        config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
    )

    assert result.connector_id == "dropbox"
    assert result.provider == AuthProvider.COMPOSIO
    assert result.config_source == AuthConfigSource.SYSTEM_DEFAULT
    auth_config_repo.create.assert_awaited_once()
    uow.commit.assert_awaited_once()


async def test_initiate_connect_request_allows_reauth_for_unusable_account():
    # An account that has become unusable (REAUTH_REQUIRED) must not block a new
    # connect request — the user reconnects in place, preserving account_id.
    user_id = uuid4()
    auth_config = _auth_config()
    unusable = _account(user_id)
    unusable.status = AccountStatus.REAUTH_REQUIRED

    auth_provider = AsyncMock()
    auth_provider.get_authorization_url.return_value = ("https://auth", "provider_state")
    registry = Mock()
    registry.get.return_value = auth_provider
    connect_repo = AsyncMock()
    connect_repo.create.side_effect = lambda req: req
    redirect_builder = Mock()
    redirect_builder.build.return_value = "https://callback"

    service = _service(
        connector_repository=AsyncMock(get=AsyncMock(return_value=_connector())),
        auth_config_repository=_auth_config_repo(auth_config),
        account_repository=AsyncMock(
            get_by_user_and_auth_config=AsyncMock(return_value=unusable)
        ),
        connect_request_repository=connect_repo,
        auth_provider_registry=registry,
        redirect_uri_builder=redirect_builder,
    )

    result = await service.initiate_connect_request(
        user_id=user_id, organization_id=ORG_ID, connector_id="slack"
    )

    assert isinstance(result, ConnectRequestEntity)


async def test_create_account_composio_api_key_connects_via_provider():
    user_id = uuid4()
    app = ConnectorEntity(
        id="airtable",
        provider_capabilities=[
            ComposioProviderCapability(
                toolkit_slug="airtable",
                auth_scheme=AuthScheme.API_KEY,
            )
        ],
    )
    auth_config = _composio_auth_config("airtable")
    stored = ComposioCredentials(connection_id="ca_airtable")

    auth_provider = AsyncMock()
    auth_provider.connect_with_credentials.return_value = stored
    registry = Mock()
    registry.get.return_value = auth_provider

    account_repo = AsyncMock()
    account_repo.get_by_user_and_auth_config.return_value = None
    account_repo.get_by_user_auth_config_and_provider_account.return_value = None
    account_repo.create.side_effect = lambda entity: entity
    uow = AsyncMock()

    service = _service(
        uow=uow,
        connector_repository=AsyncMock(get=AsyncMock(return_value=app)),
        auth_config_repository=_auth_config_repo(auth_config),
        account_repository=account_repo,
        auth_provider_registry=registry,
    )

    account = await service.create_account(
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        credentials={"api_key": "secret"},
    )

    assert account.credentials == stored
    auth_provider.connect_with_credentials.assert_awaited_once()
    account_repo.create.assert_awaited_once()
    uow.commit.assert_awaited_once()


async def test_create_account_enriches_identity_via_profile_operation():
    """Credential-managed accounts (e.g. a Notion integration token) get the
    same profile-operation enrichment OAuth accounts do -- best-effort, via
    the catalog-curated profile_operation_names on the capability."""
    user_id = uuid4()
    app = ConnectorEntity(
        id="notion",
        provider_capabilities=[
            ComposioProviderCapability(
                toolkit_slug="notion",
                auth_scheme=AuthScheme.API_KEY,
                profile_operation_names=["NOTION_RETRIEVE_YOUR_TOKEN_S_BOT_USER"],
            )
        ],
    )
    auth_config = _composio_auth_config("notion")
    stored = ComposioCredentials(connection_id="ca_notion")

    auth_provider = AsyncMock()
    auth_provider.connect_with_credentials.return_value = stored
    registry = Mock()
    registry.get.return_value = auth_provider

    operation_repository = AsyncMock()
    operation_repository.get_by_connector_provider_and_name.return_value = (
        _profile_operation("notion", "NOTION_RETRIEVE_YOUR_TOKEN_S_BOT_USER")
    )
    operation_gateway = AsyncMock()
    operation_gateway.execute_operation.return_value = {
        "email": "eng@acme.test",
        "name": "Acme Engineering",
    }

    account_repo = AsyncMock()
    account_repo.get_by_user_and_auth_config.return_value = None
    account_repo.get_by_user_auth_config_and_provider_account.return_value = None
    account_repo.create.side_effect = lambda entity: entity

    service = _service(
        connector_repository=AsyncMock(get=AsyncMock(return_value=app)),
        auth_config_repository=_auth_config_repo(auth_config),
        account_repository=account_repo,
        auth_provider_registry=registry,
        operation_gateway=operation_gateway,
        operation_repository=operation_repository,
    )

    account = await service.create_account(
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        credentials={"integration_token": "secret"},
    )

    assert account.email == "eng@acme.test"
    operation_gateway.execute_operation.assert_awaited_once()


async def test_create_account_allows_multiple_and_sets_default():
    """Multiple credential-managed accounts are allowed; the first one
    connected is the default, later ones are not."""
    user_id = uuid4()
    app = ConnectorEntity(
        id="airtable",
        provider_capabilities=[
            ComposioProviderCapability(
                toolkit_slug="airtable",
                auth_scheme=AuthScheme.API_KEY,
            )
        ],
    )
    auth_config = _composio_auth_config("airtable")
    auth_provider = AsyncMock()
    auth_provider.connect_with_credentials.return_value = ComposioCredentials(
        connection_id="ca_airtable"
    )
    registry = Mock()
    registry.get.return_value = auth_provider

    account_repo = AsyncMock()
    account_repo.create.side_effect = lambda entity: entity

    def _make_service():
        return _service(
            uow=AsyncMock(),
            connector_repository=AsyncMock(get=AsyncMock(return_value=app)),
            auth_config_repository=_auth_config_repo(auth_config),
            account_repository=account_repo,
            auth_provider_registry=registry,
        )

    # First account for the auth config -> default.
    account_repo.get_by_user_and_auth_config.return_value = None
    first = await _make_service().create_account(
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        credentials={"api_key": "one"},
    )
    assert first.is_default is True

    # A second account is allowed (no conflict) and is not the default.
    account_repo.get_by_user_and_auth_config.return_value = first
    second = await _make_service().create_account(
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        credentials={"api_key": "two"},
    )
    assert second.is_default is False


async def test_create_account_rejects_oauth2_scheme():
    user_id = uuid4()
    service = _service(
        connector_repository=AsyncMock(get=AsyncMock(return_value=_connector())),
        auth_config_repository=_auth_config_repo(_auth_config()),
    )

    with pytest.raises(ConnectorValidationError):
        await service.create_account(
            user_id=user_id,
            organization_id=ORG_ID,
            auth_config_id=_auth_config().id,
            credentials={"api_key": "secret"},
        )


async def test_get_account_credentials_marks_reauth_required_on_refresh_failure():
    user_id = uuid4()
    expired = OAuthCredentials(
        access_token="old",
        refresh_token="refresh",
        expires_at=datetime.now() - timedelta(minutes=5),
    )
    account = AccountEntity(
        id=uuid4(),
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=uuid4(),
        connector_id="slack",
        credentials=expired,
    )
    account_repo = AsyncMock()
    account_repo.get.return_value = account
    account_repo.update.side_effect = lambda entity: entity
    auth_provider = AsyncMock()
    auth_provider.refresh_credentials.side_effect = RuntimeError("token revoked")
    registry = Mock()
    registry.get.return_value = auth_provider
    uow = AsyncMock()

    service = _service(
        uow=uow,
        connector_repository=AsyncMock(get=AsyncMock(return_value=_connector())),
        auth_config_repository=_auth_config_repo(
            AuthConfigEntity(
                id=account.auth_config_id,
                organization_id=ORG_ID,
                connector_id="slack",
                provider="LEMMA",
                config_source=AuthConfigSource.SYSTEM_DEFAULT,
                name="slack",
            )
        ),
        account_repository=account_repo,
        auth_provider_registry=registry,
    )

    with pytest.raises(OAuthWorkflowError):
        await service.get_account_credentials(account.id, user_id)

    assert account.status == AccountStatus.REAUTH_REQUIRED
    account_repo.update.assert_awaited()


async def test_handle_oauth_callback_resets_status_to_connected():
    user_id = uuid4()
    auth_config = _auth_config("slack")
    connect_request = ConnectRequestEntity(
        id=uuid4(),
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        connector_id="slack",
        authorization_url="https://auth",
        status=ConnectRequestStatus.PENDING,
        attributes={"state": "state-reauth"},
    )
    existing = AccountEntity(
        id=uuid4(),
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        connector_id="slack",
        status=AccountStatus.REAUTH_REQUIRED,
        credentials=OAuthCredentials(access_token="old"),
    )
    credentials = OAuthCredentials(access_token="xoxb-token")
    auth_provider = AsyncMock()
    auth_provider.exchange_code_for_credentials.return_value = credentials
    registry = Mock()
    registry.get.return_value = auth_provider

    account_repo = AsyncMock()
    account_repo.get_by_user_and_auth_config.return_value = existing
    account_repo.update.side_effect = lambda entity: entity
    connect_repo = AsyncMock()
    connect_repo.get_by_state.return_value = connect_request
    connect_repo.update.side_effect = lambda req: req

    service = _service(
        connector_repository=AsyncMock(get=AsyncMock(return_value=_connector("slack"))),
        auth_config_repository=_auth_config_repo(auth_config),
        account_repository=account_repo,
        connect_request_repository=connect_repo,
        auth_provider_registry=registry,
    )

    with patch.object(service, "_load_native_account_profile", AsyncMock(return_value=None)):
        account = await service.handle_oauth_callback(
            redirect_uri="https://cb?state=state-reauth&code=abc",
            state="state-reauth",
        )

    assert account.status == AccountStatus.CONNECTED


async def test_handle_oauth_callback_requires_state():
    service = _service()

    with pytest.raises(ConnectRequestStateRequiredError):
        await service.handle_oauth_callback(redirect_uri="https://cb", state=None)


async def test_list_accounts_with_connector_filter_returns_empty_when_missing():
    service = _service(
        account_repository=AsyncMock(
            list_by_user_and_org=AsyncMock(return_value=([], None))
        ),
    )

    accounts, next_cursor = await service.list_accounts(
        user_id=uuid4(),
        organization_id=ORG_ID,
        connector_id="missing",
    )

    assert accounts == []
    assert next_cursor is None


async def test_list_accounts_uses_repository_cursor_pagination():
    user_id = uuid4()
    account = _account(user_id)
    account_repo = AsyncMock()
    account_repo.list_by_user_and_org.return_value = ([account], account.id)
    service = _service(
        account_repository=account_repo,
    )

    accounts, next_cursor = await service.list_accounts(
        user_id=user_id,
        organization_id=ORG_ID,
        limit=25,
        cursor=account.id,
    )

    assert accounts == [account]
    assert next_cursor == account.id
    account_repo.list_by_user_and_org.assert_awaited_once_with(
        user_id,
        ORG_ID,
        connector_id=None,
        limit=25,
        cursor=account.id,
    )


async def test_get_account_credentials_refreshes_expired_token():
    user_id = uuid4()
    expired_credentials = OAuthCredentials(
        access_token="old",
        refresh_token="refresh",
        expires_at=datetime.now() - timedelta(minutes=5),
    )
    refreshed_credentials = OAuthCredentials(
        access_token="new",
        refresh_token="refresh",
        expires_at=datetime.now() + timedelta(minutes=5),
    )
    account = AccountEntity(
        id=uuid4(),
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=uuid4(),
        connector_id="slack",
        credentials=expired_credentials,
    )
    account_repo = AsyncMock()
    account_repo.get.return_value = account
    account_repo.update.return_value = AccountEntity(
        id=account.id,
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=account.auth_config_id,
        connector_id="slack",
        credentials=refreshed_credentials,
    )
    auth_provider = AsyncMock()
    auth_provider.refresh_credentials.return_value = refreshed_credentials
    registry = Mock()
    registry.get.return_value = auth_provider
    uow = AsyncMock()

    service = _service(
        uow=uow,
        connector_repository=AsyncMock(get=AsyncMock(return_value=_connector())),
        auth_config_repository=_auth_config_repo(
            AuthConfigEntity(
                id=account.auth_config_id,
                organization_id=ORG_ID,
                connector_id="slack",
                provider="LEMMA",
                config_source=AuthConfigSource.SYSTEM_DEFAULT,
                name="slack",
            )
        ),
        account_repository=account_repo,
        auth_provider_registry=registry,
    )

    credentials = await service.get_account_credentials(account.id, user_id)

    assert credentials.access_token == "new"
    account_repo.update.assert_awaited_once()
    uow.commit.assert_awaited_once()


async def test_get_account_credentials_force_refreshes_valid_token():
    user_id = uuid4()
    valid_credentials = OAuthCredentials(
        access_token="old",
        refresh_token="refresh",
        expires_at=datetime.now() + timedelta(minutes=5),
    )
    refreshed_credentials = OAuthCredentials(
        access_token="new",
        refresh_token="refresh",
        expires_at=datetime.now() + timedelta(minutes=10),
    )
    account = AccountEntity(
        id=uuid4(),
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=uuid4(),
        connector_id="slack",
        credentials=valid_credentials,
    )
    account_repo = AsyncMock()
    account_repo.get.return_value = account
    account_repo.update.return_value = AccountEntity(
        id=account.id,
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=account.auth_config_id,
        connector_id="slack",
        credentials=refreshed_credentials,
    )
    auth_provider = AsyncMock()
    auth_provider.refresh_credentials.return_value = refreshed_credentials
    registry = Mock()
    registry.get.return_value = auth_provider
    uow = AsyncMock()

    service = _service(
        uow=uow,
        connector_repository=AsyncMock(get=AsyncMock(return_value=_connector())),
        auth_config_repository=_auth_config_repo(
            AuthConfigEntity(
                id=account.auth_config_id,
                organization_id=ORG_ID,
                connector_id="slack",
                provider="LEMMA",
                config_source=AuthConfigSource.SYSTEM_DEFAULT,
                name="slack",
            )
        ),
        account_repository=account_repo,
        auth_provider_registry=registry,
    )

    credentials = await service.get_account_credentials(
        account.id,
        user_id,
        force_refresh=True,
    )

    assert credentials.access_token == "new"
    account_repo.update.assert_awaited_once()
    uow.commit.assert_awaited_once()


async def test_handle_oauth_callback_sets_provider_account_id_on_create():
    user_id = uuid4()
    auth_config = _auth_config("slack")
    connect_request = ConnectRequestEntity(
        id=uuid4(),
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        connector_id="slack",
        authorization_url="https://auth",
        status=ConnectRequestStatus.PENDING,
        attributes={"state": "state-1"},
    )
    credentials = OAuthCredentials(
        access_token="xoxb-token",
        raw_response={"authed_user": {"id": "U077RUS3FS7"}},
    )
    auth_provider = AsyncMock()
    auth_provider.exchange_code_for_credentials.return_value = credentials
    registry = Mock()
    registry.get.return_value = auth_provider

    account_repo = AsyncMock()
    account_repo.get_by_user_and_auth_config.return_value = None
    account_repo.get_by_user_auth_config_and_provider_account.return_value = None
    account_repo.create.side_effect = lambda entity: entity
    connect_repo = AsyncMock()
    connect_repo.get_by_state.return_value = connect_request
    connect_repo.update.side_effect = lambda req: req

    service = _service(
        connector_repository=AsyncMock(get=AsyncMock(return_value=_connector("slack"))),
        auth_config_repository=_auth_config_repo(auth_config),
        account_repository=account_repo,
        connect_request_repository=connect_repo,
        auth_provider_registry=registry,
    )

    with patch.object(service, "_load_native_account_profile", AsyncMock(return_value=None)):
        account = await service.handle_oauth_callback(
            redirect_uri="https://cb?state=state-1&code=abc",
            state="state-1",
        )

    assert account.provider_account_id == "U077RUS3FS7"
    account_repo.create.assert_awaited_once()


async def test_handle_oauth_callback_enriches_slack_account_profile():
    user_id = uuid4()
    auth_config = _auth_config("slack")
    connect_request = ConnectRequestEntity(
        id=uuid4(),
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        connector_id="slack",
        authorization_url="https://auth",
        status=ConnectRequestStatus.PENDING,
        attributes={"state": "state-profile"},
    )
    credentials = OAuthCredentials(access_token="xoxb-token")
    auth_provider = AsyncMock()
    auth_provider.exchange_code_for_credentials.return_value = credentials
    registry = Mock()
    registry.get.return_value = auth_provider

    native_client = AsyncMock()
    native_client.execute_operation.side_effect = [
        {
            "ok": True,
            "team": "Acme",
            "team_id": "T123",
            "url": "https://acme.slack.com/",
            "user": "Rahul",
            "user_id": "U123",
            "bot_id": "B123",
        },
        {
            "ok": True,
            "user": {
                "id": "U123",
                "name": "rahul",
                "profile": {
                    "email": "rahul@example.com",
                    "real_name": "Rahul",
                },
            },
        },
    ]
    account_repo = AsyncMock()
    account_repo.get_by_user_and_auth_config.return_value = None
    account_repo.get_by_user_auth_config_and_provider_account.return_value = None
    account_repo.create.side_effect = lambda entity: entity
    connect_repo = AsyncMock()
    connect_repo.get_by_state.return_value = connect_request
    connect_repo.update.side_effect = lambda req: req

    service = _service(
        connector_repository=AsyncMock(get=AsyncMock(return_value=_connector("slack"))),
        auth_config_repository=_auth_config_repo(auth_config),
        account_repository=account_repo,
        connect_request_repository=connect_repo,
        auth_provider_registry=registry,
    )

    with patch(
        "app.modules.connectors.services.connector_service.create_lemma_execution_client",
        return_value=native_client,
    ):
        account = await service.handle_oauth_callback(
            redirect_uri="https://cb?state=state-profile&code=abc",
            state="state-profile",
        )

    assert account.provider_account_id == "U123"
    assert account.email == "rahul@example.com"
    assert account.credentials.user_data["profile"]["team"] == "Acme"
    assert account.credentials.user_data["profile"]["user_info"]["user"]["id"] == "U123"
    native_client.execute_operation.assert_any_await(
        "auth_test",
        {"token": "xoxb-token"},
    )
    native_client.execute_operation.assert_any_await(
        "users_info",
        {"token": "xoxb-token", "user": "U123"},
    )


async def test_handle_oauth_callback_updates_provider_account_id_on_existing_account():
    user_id = uuid4()
    auth_config = _auth_config("slack")
    connect_request = ConnectRequestEntity(
        id=uuid4(),
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        connector_id="slack",
        authorization_url="https://auth",
        status=ConnectRequestStatus.PENDING,
        attributes={"state": "state-2"},
    )
    existing = AccountEntity(
        id=uuid4(),
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        connector_id="slack",
        provider_account_id=None,
        credentials=OAuthCredentials(access_token="old"),
    )
    credentials = OAuthCredentials(
        access_token="xoxb-token",
        raw_response={"authed_user": {"id": "U0999999999"}},
    )
    auth_provider = AsyncMock()
    auth_provider.exchange_code_for_credentials.return_value = credentials
    registry = Mock()
    registry.get.return_value = auth_provider

    account_repo = AsyncMock()
    account_repo.get_by_user_and_auth_config.return_value = existing
    account_repo.update.side_effect = lambda entity: entity
    connect_repo = AsyncMock()
    connect_repo.get_by_state.return_value = connect_request
    connect_repo.update.side_effect = lambda req: req

    service = _service(
        connector_repository=AsyncMock(get=AsyncMock(return_value=_connector("slack"))),
        auth_config_repository=_auth_config_repo(auth_config),
        account_repository=account_repo,
        connect_request_repository=connect_repo,
        auth_provider_registry=registry,
    )

    with patch.object(service, "_load_native_account_profile", AsyncMock(return_value=None)):
        account = await service.handle_oauth_callback(
            redirect_uri="https://cb?state=state-2&code=abc",
            state="state-2",
        )

    assert account.provider_account_id == "U0999999999"
    account_repo.update.assert_awaited_once()


def _composio_auth_config(connector_id: str) -> AuthConfigEntity:
    return AuthConfigEntity(
        id=uuid4(),
        organization_id=ORG_ID,
        connector_id=connector_id,
        provider="COMPOSIO",
        config_source=AuthConfigSource.SYSTEM_DEFAULT,
        name=connector_id,
    )


def _profile_operation(
    connector_id: str, name: str
) -> ConnectorOperationEntity:
    return ConnectorOperationEntity(
        id=f"{connector_id}:{name.lower()}",
        connector_id=connector_id,
        provider=AuthProvider.COMPOSIO,
        name=name,
        provider_operation_name=name,
    )


async def test_handle_oauth_callback_populates_email_via_profile_operation():
    """Email is filled from a provider-agnostic get-profile operation, so a
    Composio Outlook account gets its `mail`/`userPrincipalName` populated.

    The mocked gateway result is wrapped in Composio's real envelope
    (composio.tools.execute's ToolExecutionResponse: {data, error, successful})
    rather than a flat dict, so this actually exercises the unwrapping in
    _fetch_account_profile instead of accidentally passing regardless of it.
    """
    user_id = uuid4()
    auth_config = _composio_auth_config("outlook")
    connect_request = ConnectRequestEntity(
        id=uuid4(),
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        connector_id="outlook",
        authorization_url="https://auth",
        status=ConnectRequestStatus.PENDING,
        attributes={"state": "state-outlook"},
    )
    credentials = OAuthCredentials(access_token="tok", connection_id="ca_123")
    auth_provider = AsyncMock()
    auth_provider.exchange_code_for_credentials.return_value = credentials
    registry = Mock()
    registry.get.return_value = auth_provider

    operation_gateway = AsyncMock()
    operation_gateway.execute_operation.return_value = {
        "data": {
            "displayName": "Test User",
            "mail": "user@lemma.work",
            "userPrincipalName": "user@lemma.work",
        },
        "error": None,
        "successful": True,
    }
    operation_repository = AsyncMock()
    operation_repository.get_by_connector_provider_and_name.return_value = (
        _profile_operation("outlook", "OUTLOOK_GET_PROFILE")
    )

    account_repo = AsyncMock()
    account_repo.get_by_user_and_auth_config.return_value = None
    account_repo.get_by_user_auth_config_and_provider_account.return_value = None
    account_repo.create.side_effect = lambda entity: entity
    connect_repo = AsyncMock()
    connect_repo.get_by_state.return_value = connect_request
    connect_repo.update.side_effect = lambda req: req

    outlook_app = ConnectorEntity(
        id="outlook",
        provider_capabilities=[
            ComposioProviderCapability(
                toolkit_slug="outlook",
                profile_operation_names=["OUTLOOK_GET_PROFILE"],
            ),
        ],
    )
    service = _service(
        connector_repository=AsyncMock(get=AsyncMock(return_value=outlook_app)),
        auth_config_repository=_auth_config_repo(auth_config),
        account_repository=account_repo,
        connect_request_repository=connect_repo,
        auth_provider_registry=registry,
        operation_gateway=operation_gateway,
        operation_repository=operation_repository,
    )

    with patch.object(
        service, "_load_native_account_profile", AsyncMock(return_value=None)
    ):
        account = await service.handle_oauth_callback(
            redirect_uri="https://cb?state=state-outlook&code=abc",
            state="state-outlook",
        )

    assert account.email == "user@lemma.work"
    operation_repository.get_by_connector_provider_and_name.assert_awaited_with(
        "outlook", "COMPOSIO", "OUTLOOK_GET_PROFILE"
    )
    execute_kwargs = operation_gateway.execute_operation.await_args.kwargs
    assert execute_kwargs["operation_name"] == "OUTLOOK_GET_PROFILE"
    assert execute_kwargs["provider"] == "COMPOSIO"
    assert execute_kwargs["third_party_credentials"]["connection_id"] == "ca_123"


async def test_fetch_account_profile_skips_when_gateway_absent():
    service = _service()
    result = await service._fetch_account_profile(
        _connector("outlook"), "COMPOSIO", OAuthCredentials(access_token="tok")
    )
    assert result is None


async def test_fetch_account_profile_unwraps_composio_data_envelope():
    """Every composio.tools.execute() result is wrapped in
    {"data": ..., "error": ..., "successful": ...} -- the toolkit's actual
    fields live under `data`, not at the top level. Without unwrapping this,
    email/identity extraction would never find anything for any Composio app."""
    connector = ConnectorEntity(
        id="asana",
        provider_capabilities=[
            ComposioProviderCapability(
                toolkit_slug="asana",
                profile_operation_names=["ASANA_GET_CURRENT_USER"],
            ),
        ],
    )
    operation_repository = AsyncMock()
    operation_repository.get_by_connector_provider_and_name.return_value = (
        _profile_operation("asana", "ASANA_GET_CURRENT_USER")
    )
    operation_gateway = AsyncMock()
    operation_gateway.execute_operation.return_value = {
        "data": {"email": "pm@acme.test", "name": "Project Manager"},
        "error": None,
        "successful": True,
    }

    service = _service(
        operation_gateway=operation_gateway,
        operation_repository=operation_repository,
    )
    result = await service._fetch_account_profile(
        connector, "COMPOSIO", OAuthCredentials(access_token="tok")
    )

    assert result == {"email": "pm@acme.test", "name": "Project Manager"}


async def test_fetch_account_profile_does_not_unwrap_for_lemma_provider():
    """Native (Lemma) operation results are never Composio-wrapped -- a
    coincidental top-level "data" key must be left alone."""
    connector = ConnectorEntity(
        id="gmail",
        provider_capabilities=[
            LemmaProviderCapability(profile_operation_names=["get_profile"]),
        ],
    )
    operation_repository = AsyncMock()
    operation_repository.get_by_connector_provider_and_name.return_value = (
        _profile_operation("gmail", "get_profile")
    )
    operation_gateway = AsyncMock()
    operation_gateway.execute_operation.return_value = {
        "email_address": "user@gmail.com",
        "data": "not an envelope",
    }

    service = _service(
        operation_gateway=operation_gateway,
        operation_repository=operation_repository,
    )
    result = await service._fetch_account_profile(
        connector, "LEMMA", OAuthCredentials(access_token="tok")
    )

    assert result == {
        "email_address": "user@gmail.com",
        "data": "not an envelope",
    }


async def test_fetch_account_profile_skips_when_provider_unsupported():
    """capability_for raises for a provider the connector doesn't support --
    must be swallowed, not propagated, since this is a best-effort lookup."""
    service = _service(
        operation_gateway=AsyncMock(), operation_repository=AsyncMock()
    )
    result = await service._fetch_account_profile(
        _connector("slack"), "COMPOSIO", OAuthCredentials(access_token="tok")
    )
    assert result is None


async def test_fetch_account_profile_tries_catalog_operation_names_in_order():
    connector = ConnectorEntity(
        id="asana",
        provider_capabilities=[
            ComposioProviderCapability(
                toolkit_slug="asana",
                profile_operation_names=["ASANA_MISSING_OP", "ASANA_GET_CURRENT_USER"],
            ),
        ],
    )
    operation_repository = AsyncMock()
    operation_repository.get_by_connector_provider_and_name.side_effect = [
        None,
        _profile_operation("asana", "ASANA_GET_CURRENT_USER"),
    ]
    operation_gateway = AsyncMock()
    operation_gateway.execute_operation.return_value = {"email": "pm@acme.test"}

    service = _service(
        operation_gateway=operation_gateway,
        operation_repository=operation_repository,
    )
    result = await service._fetch_account_profile(
        connector, "COMPOSIO", OAuthCredentials(access_token="tok")
    )

    assert result == {"email": "pm@acme.test"}
    assert operation_repository.get_by_connector_provider_and_name.await_count == 2


async def test_handle_oauth_callback_surfaces_upstream_error_details():
    user_id = uuid4()
    auth_config = _auth_config("slack")
    connect_request = ConnectRequestEntity(
        id=uuid4(),
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        connector_id="slack",
        authorization_url="https://auth",
        status=ConnectRequestStatus.PENDING,
        attributes={"state": "state-3"},
    )
    auth_provider = AsyncMock()
    auth_provider.exchange_code_for_credentials.side_effect = RuntimeError(
        "provider broke"
    )
    registry = Mock()
    registry.get.return_value = auth_provider
    connect_repo = AsyncMock()
    connect_repo.get_by_state.return_value = connect_request

    service = _service(
        connector_repository=AsyncMock(get=AsyncMock(return_value=_connector("slack"))),
        auth_config_repository=_auth_config_repo(auth_config),
        account_repository=AsyncMock(),
        connect_request_repository=connect_repo,
        auth_provider_registry=registry,
    )

    with pytest.raises(OAuthWorkflowError) as exc_info:
        await service.handle_oauth_callback(
            redirect_uri="https://cb?state=state-3&code=abc",
            state="state-3",
        )

    assert exc_info.value.details == {"error_type": "RuntimeError"}
    assert "provider broke" not in str(exc_info.value)
    connect_repo.update.assert_awaited_once()


async def test_reauth_new_identity_does_not_clobber_null_provider_default():
    """A re-auth for a *different* provider identity must not overwrite the
    default account when the default's provider_account_id is null — it creates a
    new account instead (regression for the credential-clobber bug)."""
    user_id = uuid4()
    auth_config = _auth_config("slack")
    connect_request = ConnectRequestEntity(
        id=uuid4(),
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        connector_id="slack",
        authorization_url="https://auth",
        status=ConnectRequestStatus.PENDING,
        attributes={"state": "state-clobber"},
    )
    credentials = OAuthCredentials(
        access_token="xoxb-new-identity",
        raw_response={"authed_user": {"id": "U_NEW_IDENTITY"}},
    )
    auth_provider = AsyncMock()
    auth_provider.exchange_code_for_credentials.return_value = credentials
    registry = Mock()
    registry.get.return_value = auth_provider

    # The user's default account has a NULL provider_account_id.
    default_account = _account(user_id, "slack")
    default_account.is_default = True
    default_account.provider_account_id = None

    account_repo = AsyncMock()
    account_repo.get_by_user_and_auth_config.return_value = default_account
    # No account matches the incoming (different) provider identity.
    account_repo.get_by_user_auth_config_and_provider_account.return_value = None
    account_repo.create.side_effect = lambda entity: entity
    connect_repo = AsyncMock()
    connect_repo.get_by_state.return_value = connect_request
    connect_repo.update.side_effect = lambda req: req

    service = _service(
        connector_repository=AsyncMock(get=AsyncMock(return_value=_connector("slack"))),
        auth_config_repository=_auth_config_repo(auth_config),
        account_repository=account_repo,
        connect_request_repository=connect_repo,
        auth_provider_registry=registry,
    )

    with patch.object(service, "_load_native_account_profile", AsyncMock(return_value=None)):
        account = await service.handle_oauth_callback(
            redirect_uri="https://cb?state=state-clobber&code=abc",
            state="state-clobber",
        )

    # A NEW account was created for the new identity; the default was not touched.
    account_repo.create.assert_awaited_once()
    account_repo.update.assert_not_awaited()
    assert account.provider_account_id == "U_NEW_IDENTITY"
    assert account.is_default is False


async def test_delete_default_account_promotes_next_default():
    """Deleting the default account promotes the oldest remaining one so the
    'exactly one default per (user, auth_config)' invariant survives."""
    user_id = uuid4()
    default_account = _account(user_id, "telegram")
    default_account.is_default = True

    account_repo = AsyncMock()
    service = _service(
        account_repository=account_repo,
        connector_repository=AsyncMock(get=AsyncMock(return_value=_connector("telegram"))),
    )
    service.get_account = AsyncMock(return_value=default_account)
    service._resolve_auth_config = AsyncMock(return_value=_auth_config("telegram"))
    service._should_revoke_account = Mock(return_value=False)

    await service.delete_account(default_account.id, user_id)

    account_repo.delete.assert_awaited_once_with(default_account.id)
    account_repo.promote_next_default.assert_awaited_once()
    kwargs = account_repo.promote_next_default.await_args.kwargs
    assert kwargs["user_id"] == user_id
    assert kwargs["auth_config_id"] == default_account.auth_config_id
    assert kwargs["exclude_account_id"] == default_account.id


async def test_delete_non_default_account_does_not_promote():
    user_id = uuid4()
    account = _account(user_id, "telegram")
    account.is_default = False

    account_repo = AsyncMock()
    service = _service(
        account_repository=account_repo,
        connector_repository=AsyncMock(get=AsyncMock(return_value=_connector("telegram"))),
    )
    service.get_account = AsyncMock(return_value=account)
    service._resolve_auth_config = AsyncMock(return_value=_auth_config("telegram"))
    service._should_revoke_account = Mock(return_value=False)

    await service.delete_account(account.id, user_id)

    account_repo.delete.assert_awaited_once_with(account.id)
    account_repo.promote_next_default.assert_not_awaited()


def _airtable_service(*, existing_by_identity):
    app = ConnectorEntity(
        id="airtable",
        provider_capabilities=[
            ComposioProviderCapability(
                toolkit_slug="airtable", auth_scheme=AuthScheme.API_KEY
            )
        ],
    )
    auth_config = _composio_auth_config("airtable")
    auth_provider = AsyncMock()
    auth_provider.connect_with_credentials.return_value = ComposioCredentials(
        connection_id="ca_airtable"
    )
    registry = Mock()
    registry.get.return_value = auth_provider

    account_repo = AsyncMock()
    account_repo.get_by_user_and_auth_config.return_value = None
    account_repo.get_by_user_auth_config_and_provider_account.return_value = (
        existing_by_identity
    )
    account_repo.create.side_effect = lambda entity: entity

    service = _service(
        uow=AsyncMock(),
        connector_repository=AsyncMock(get=AsyncMock(return_value=app)),
        auth_config_repository=_auth_config_repo(auth_config),
        account_repository=account_repo,
        auth_provider_registry=registry,
    )
    return service, auth_config, account_repo


async def test_create_account_rejects_duplicate_connected_identity():
    """Connecting the same provider identity again while a healthy account
    already exists is rejected."""
    user_id = uuid4()
    existing = _account(user_id, "airtable")  # status CONNECTED by default
    service, auth_config, _ = _airtable_service(existing_by_identity=existing)

    with pytest.raises(AccountAlreadyConnectedError):
        await service.create_account(
            user_id=user_id,
            organization_id=ORG_ID,
            auth_config_id=auth_config.id,
            provider_account_id="acc-dup",
            credentials={"api_key": "x"},
        )


async def test_create_account_allows_when_existing_identity_unhealthy():
    """An unhealthy (needs-reauth) account for the same identity does not block a
    new connect."""
    user_id = uuid4()
    existing = _account(user_id, "airtable")
    existing.status = AccountStatus.REAUTH_REQUIRED
    service, auth_config, account_repo = _airtable_service(existing_by_identity=existing)

    account = await service.create_account(
        user_id=user_id,
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        provider_account_id="acc-dup",
        credentials={"api_key": "x"},
    )
    account_repo.create.assert_awaited_once()
    assert account.provider_account_id == "acc-dup"
