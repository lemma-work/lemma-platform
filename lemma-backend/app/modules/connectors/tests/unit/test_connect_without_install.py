"""Connecting a connector nobody in the organization installed yet.

A new organization has no installs, so every first connect by connector id
used to answer "Connector 'gmail' not found" -- to the agent and the CLI alike;
only the web app installed one first, client-side. And every click of Connect
made another pending request and another provider-side authorization.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigSource,
    AuthConfigStatus,
)
from app.modules.connectors.domain.connect_request import (
    ConnectRequestEntity,
    ConnectRequestStatus,
)
from app.modules.connectors.domain.errors import (
    ConnectorNotInstalledError,
    ConnectorValidationError,
    InstallationNotFoundError,
)
from app.modules.connectors.services.connector_service import ConnectorService
from app.modules.connectors.services.install_on_connect import install_to_connect
from app.modules.connectors.tests.unit.test_connector_service import (
    ORG_ID,
    _auth_config,
    _auth_config_repo,
    _auth_provider,
    _connector,
    _org_access,
    _service,
)

pytestmark = pytest.mark.asyncio


def _service_with_no_install(
    *, may_install: bool, made: AuthConfigEntity
) -> tuple[ConnectorService, AsyncMock]:
    repository = AsyncMock(
        get_active_by_org_and_app=AsyncMock(return_value=None),
        get_active_by_org_app_and_source=AsyncMock(return_value=None),
        get_disabled_by_org_and_app=AsyncMock(return_value=None),
        get_active_by_org_and_name=AsyncMock(return_value=None),
    )
    access = _org_access()
    access.user_holds_organization_permission = AsyncMock(return_value=may_install)
    service = _service(
        auth_config_repository=repository,
        organization_access=access,
        connector_repository=AsyncMock(get=AsyncMock(return_value=_connector("gmail"))),
    )
    create = AsyncMock(return_value=made)
    service.create_auth_config = create  # type: ignore[method-assign]
    return service, create


async def _install_gmail(service: ConnectorService) -> AuthConfigEntity:
    return await install_to_connect(
        service,
        user_id=uuid4(),
        organization_id=ORG_ID,
        connector_id="gmail",
        auth_config_id=None,
    )


async def test_someone_who_may_install_gets_lemmas_own_app_installed():
    made = _auth_config("gmail")
    service, create = _service_with_no_install(may_install=True, made=made)

    assert await _install_gmail(service) is made
    assert (
        create.call_args.kwargs["config_source"]
        == AuthConfigSource.SYSTEM_DEFAULT.value
    )


async def test_anyone_else_is_told_it_is_not_installed_and_who_adds_it():
    service, create = _service_with_no_install(
        may_install=False, made=_auth_config("gmail")
    )

    with pytest.raises(ConnectorNotInstalledError) as refused:
        await _install_gmail(service)

    assert refused.value.code == "CONNECTOR_NOT_INSTALLED"
    assert "owner or editor" in refused.value.message
    create.assert_not_awaited()


async def test_an_install_an_admin_switched_off_is_not_made_again():
    service, create = _service_with_no_install(
        may_install=True, made=_auth_config("gmail")
    )
    switched_off = _auth_config("gmail").model_copy(
        update={"status": AuthConfigStatus.DISABLED}
    )
    service.auth_config_repository.get_disabled_by_org_and_app = AsyncMock(  # type: ignore[method-assign]
        return_value=switched_off
    )

    with pytest.raises(ConnectorValidationError):
        await _install_gmail(service)
    create.assert_not_awaited()


async def test_an_installation_name_nobody_uses_is_named_as_that():
    service = _service(
        auth_config_repository=AsyncMock(
            get_active_by_org_and_name=AsyncMock(return_value=None)
        ),
        connector_repository=AsyncMock(get=AsyncMock(return_value=None)),
    )

    with pytest.raises(InstallationNotFoundError):
        await service.get_auth_config_by_name(
            user_id=uuid4(), organization_id=ORG_ID, auth_config_name="crm-2"
        )


async def test_a_catalog_connector_named_as_an_installation_says_not_installed():
    service = _service(
        auth_config_repository=AsyncMock(
            get_active_by_org_and_name=AsyncMock(return_value=None)
        ),
        connector_repository=AsyncMock(get=AsyncMock(return_value=_connector("gmail"))),
    )

    with pytest.raises(ConnectorNotInstalledError):
        await service.get_auth_config_by_name(
            user_id=uuid4(), organization_id=ORG_ID, auth_config_name="gmail"
        )


async def test_asking_again_for_the_same_sign_in_hands_back_the_open_one():
    auth_config = _auth_config()
    pending = ConnectRequestEntity(
        id=uuid4(),
        user_id=uuid4(),
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        connector_id="slack",
        authorization_url="https://auth/once",
        status=ConnectRequestStatus.PENDING,
        attributes={"state": "s", "return_to": "/pods/p/connections"},
    )
    provider = _auth_provider()
    connect_repo = AsyncMock(get_reusable_pending=AsyncMock(return_value=pending))
    service = _service(
        auth_config_repository=_auth_config_repo(auth_config),
        connect_request_repository=connect_repo,
        auth_provider_registry=Mock(get=Mock(return_value=provider)),
    )

    again = await service.initiate_connect_request(
        user_id=pending.user_id,
        organization_id=ORG_ID,
        connector_id="slack",
        return_to="/pods/p/connections",
    )

    assert again is pending
    provider.get_authorization_url.assert_not_called()
    connect_repo.create.assert_not_called()
    assert (
        connect_repo.get_reusable_pending.call_args.kwargs["return_to"]
        == "/pods/p/connections"
    )


async def test_a_sign_in_built_for_one_call_is_never_reused():
    auth_config = _auth_config()
    provider = _auth_provider()
    provider.get_authorization_url.return_value = ("https://auth/new", "ps")
    connect_repo = AsyncMock()
    connect_repo.create.side_effect = lambda request: request
    service = _service(
        auth_config_repository=_auth_config_repo(auth_config),
        connect_request_repository=connect_repo,
        auth_provider_registry=Mock(get=Mock(return_value=provider)),
        redirect_uri_builder=Mock(build=Mock(return_value="https://cb")),
    )

    made = await service.initiate_connect_request(
        user_id=uuid4(),
        organization_id=ORG_ID,
        auth_config_id=auth_config.id,
        omit_authorize_params=frozenset({"user_scope"}),
    )

    assert made.authorization_url == "https://auth/new"
    connect_repo.get_reusable_pending.assert_not_called()
