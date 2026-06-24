import importlib.util
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from composio import Composio
from httpx import AsyncClient
from sqlalchemy import delete, select

sys.path.append(str(Path(__file__).resolve().parents[5]))

from app.modules.connectors.config import connector_settings
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.account import OAuthCredentials
from app.modules.connectors.domain.connector import (
    AuthMethod,
    AuthProvider,
    ComposioProviderCapability,
    LemmaProviderCapability,
)
from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.infrastructure.adapters.schema_compiler import (
    PydanticCodeSchemaCompiler,
)
from app.modules.connectors.infrastructure.adapters.composio_operation_gateway import (
    ComposioOperationGateway,
)
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.models.connector_operation import (
    ConnectorOperation,
)
from app.modules.connectors.infrastructure.models.connector_trigger import (
    ConnectorTrigger,
)
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.repositories.connector_operation_repository import (
    ConnectorOperationRepository,
)
from app.modules.connectors.infrastructure.repositories.connector_repository import (
    ConnectorRepository,
)
from app.modules.connectors.infrastructure.repositories.connector_trigger_repository import (
    ConnectorTriggerRepository,
)
from app.modules.connectors.services.connector_service import ConnectorService


class FakeProviderOperationError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, details=None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details


def _provider_capability(provider: str, auth_method: str) -> dict:
    if provider == AuthProvider.COMPOSIO.value:
        return ComposioProviderCapability(
            provider=AuthProvider.COMPOSIO,
            auth_scheme=AuthMethod(auth_method),
            toolkit_slug="googlecalendar",
        ).model_dump(mode="json")
    return LemmaProviderCapability(
        provider=AuthProvider.LEMMA,
        auth_scheme=AuthMethod(auth_method),
    ).model_dump(mode="json")


def _connector(
    *,
    app_id: str,
    title: str,
    description: str,
    provider: str,
    auth_method: str,
) -> Connector:
    return Connector(
        id=app_id,
        title=title,
        description=description,
        provider_capabilities=[_provider_capability(provider, auth_method)],
        is_active=True,
    )


async def _seed_auth_config(
    db_session,
    *,
    app_id: str,
    organization_id: str,
    provider: str = AuthProvider.LEMMA.value,
) -> AuthConfig:
    auth_config = AuthConfig(
        organization_id=organization_id,
        connector_id=app_id,
        provider=provider,
        config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
        name=f"{app_id}-{uuid4().hex[:8]}",
    )
    db_session.add(auth_config)
    await db_session.flush()
    return auth_config


TEST_GOOGLE_CALENDAR_COMPOSIO_ACCOUNT_ID_ENV = (
    "TEST_GOOGLE_CALENDAR_COMPOSIO_ACCOUNT_ID"
)
_IMPORTER_MODULE_PATH = (
    Path(__file__).resolve().parents[5] / "scripts" / "import_connector_catalog.py"
)
_IMPORTER_SPEC = importlib.util.spec_from_file_location(
    "import_connector_catalog", _IMPORTER_MODULE_PATH
)
assert _IMPORTER_SPEC and _IMPORTER_SPEC.loader
importer = importlib.util.module_from_spec(_IMPORTER_SPEC)
_IMPORTER_SPEC.loader.exec_module(importer)


def _read_env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value:
        return value

    env_path = Path(__file__).resolve().parents[5] / ".env"
    if not env_path.exists():
        return None

    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if key.strip() == name:
            return raw_value.strip()
    return None


async def _reseed_google_calendar_catalog(db_session) -> None:
    await db_session.execute(
        delete(ConnectorTrigger).where(
            ConnectorTrigger.connector_id == "google_calendar"
        )
    )
    await db_session.execute(
        delete(ConnectorOperation).where(
            ConnectorOperation.connector_id == "google_calendar"
        )
    )
    await db_session.execute(
        delete(Connector).where(Connector.id == "google_calendar")
    )
    await db_session.commit()

    uow = SqlAlchemyUnitOfWork(db_session)
    connector_repository = ConnectorRepository(uow)
    operation_repository = ConnectorOperationRepository(uow)
    trigger_repository = ConnectorTriggerRepository(uow)

    await importer._sync_native_catalog(
        connector_repository,
        operation_repository,
        trigger_repository,
        app_filters={"google_calendar"},
        schema_compiler=PydanticCodeSchemaCompiler(),
    )
    await importer._sync_composio_catalog(
        connector_repository,
        operation_repository,
        trigger_repository,
        app_filters={"google_calendar"},
        managed_by="composio",
        page_size=100,
        max_composio_apps=10,
    )
    await uow.commit()


async def _seed_real_google_calendar_account(db_session, *, user_id) -> str:
    connection_id = _read_env_value(TEST_GOOGLE_CALENDAR_COMPOSIO_ACCOUNT_ID_ENV)
    if not connection_id:
        pytest.skip(
            f"Real Google Calendar e2e requires {TEST_GOOGLE_CALENDAR_COMPOSIO_ACCOUNT_ID_ENV}."
        )
    if not connector_settings.composio_api_key:
        pytest.skip("Real Google Calendar e2e requires COMPOSIO_API_KEY.")

    try:
        Composio(api_key=connector_settings.composio_api_key).connected_accounts.get(
            connection_id
        )
    except Exception as exc:
        pytest.skip(
            "Real Google Calendar e2e requires a live Composio connected account; "
            f"{connection_id} was not available upstream: {exc}"
        )

    await _reseed_google_calendar_catalog(db_session)

    connector = await db_session.get(Connector, "google_calendar")
    assert connector is not None
    capability = connector.to_entity().capability_for(AuthProvider.COMPOSIO)
    assert capability.provider == AuthProvider.COMPOSIO
    assert capability.auth_scheme == AuthMethod.OAUTH2

    list_events_operation = await db_session.execute(
        select(ConnectorOperation).where(
            ConnectorOperation.connector_id == "google_calendar",
            ConnectorOperation.name == "list_events",
        )
    )
    operation = list_events_operation.scalars().first()
    assert operation is not None
    assert operation.provider_operation_name == "list_events"

    existing_account = await db_session.execute(
        select(Account).where(
            Account.user_id == user_id,
            Account.connector_id == "google_calendar",
        )
    )
    account = existing_account.scalars().first()
    if account is None:
        account = Account(
            user_id=user_id,
            connector_id="google_calendar",
        )
        db_session.add(account)

    account.credentials = {
        "access_token": "stale-access-token",
        "token_type": "Bearer",
        "expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat(),
        "connection_id": connection_id,
    }
    await db_session.commit()
    return str(account.id)


@pytest.mark.asyncio
async def test_connector_operations_use_connected_user_account(
    authenticated_client: AsyncClient,
    fixed_test_user,
    fixed_test_org,
    db_session,
):
    app_id = "test-operation-app"

    app = await db_session.get(Connector, app_id)
    if not app:
        app = _connector(
            app_id=app_id,
            title="Test Operation App",
            description="Test App for Operations",
            provider=AuthProvider.LEMMA.value,
            auth_method=AuthMethod.API_KEY.value,
        )
        db_session.add(app)
        await db_session.flush()
    auth_config = await _seed_auth_config(
        db_session,
        app_id=app_id,
        organization_id=fixed_test_org["id"],
        provider=AuthProvider.LEMMA.value,
    )
    operations_url = (
        f"/organizations/{fixed_test_org['id']}/connectors/"
        f"{auth_config.name}/operations"
    )

    account_id = uuid4()
    account = Account(
        id=account_id,
        connector_id=app_id,
        user_id=fixed_test_user["id"],
        organization_id=fixed_test_org["id"],
        auth_config_id=auth_config.id,
        credentials={"api_key": "secret"},
    )
    db_session.add(account)
    db_session.add(
        ConnectorOperation(
            id=f"{app_id}:test_op",
            connector_id=app_id,
            name="test_op",
            provider_operation_name="test_op",
            display_name="Test Op",
            description="Test Operation Desc",
            input_schema={
                "type": "object",
                "properties": {"foo": {"type": "string"}},
                "required": ["foo"],
            },
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
            },
        )
    )
    await db_session.commit()

    mock_execution_client = AsyncMock()
    mock_execution_client.list_operations.return_value = [
        SimpleNamespace(name="test_op")
    ]
    mock_execution_client.get_operation.return_value = SimpleNamespace(descriptor=None)

    async def mock_op(_operation_name, payload):
        return f"Processed {payload.get('foo')}"

    mock_execution_client.execute_operation = AsyncMock(side_effect=mock_op)

    with patch(
        "app.modules.connectors.infrastructure.adapters.lemma_operation_gateway.create_lemma_execution_client",
        return_value=mock_execution_client,
    ) as _mock_get_exec:
        response = await authenticated_client.get(
            operations_url,
            params={"query": "test desc"},
        )
        assert response.status_code == 200, response.text
        discovery = response.json()
        ops = discovery["items"]
        assert discovery["connector_id"] == app_id
        assert discovery["total_operations"] == 1
        assert discovery["returned_count"] == 1
        assert len(ops) == 1
        assert ops[0]["name"] == "test_op"
        assert ops[0]["description"] == "Test Operation Desc"

        response = await authenticated_client.post(
            f"{operations_url}/details",
            json={"operation_names": ["test_op"]},
        )
        assert response.status_code == 200, response.text
        detail_batch = response.json()
        assert detail_batch["connector_id"] == app_id
        assert detail_batch["returned_count"] == 1
        assert detail_batch["items"][0]["name"] == "test_op"

        response = await authenticated_client.get(
            f"{operations_url}/TEST_OP"
        )
        assert response.status_code == 200, response.text
        details = response.json()
        assert details["name"] == "test_op"
        assert "properties" in details["input_schema"]
        assert "foo" in details["input_schema"]["properties"]
        assert details["input_schema"]["properties"]["foo"]["type"] == "string"

        payload = {"payload": {"foo": "bar"}, "account_id": str(account_id)}
        response = await authenticated_client.post(
            f"{operations_url}/TEST_OP/execute",
            json=payload,
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["result"] == "Processed bar"

        assert _mock_get_exec.called
        assert _mock_get_exec.call_args.args[1] == {
            "api_key": "secret"
        }


@pytest.mark.asyncio
async def test_connector_operation_discovery_uses_name_and_description_only(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session,
):
    app_id = "test-operation-search-app"

    app = await db_session.get(Connector, app_id)
    if not app:
        app = _connector(
            app_id=app_id,
            title="Test Search App",
            description="App for operation discovery search",
            provider=AuthProvider.LEMMA.value,
            auth_method=AuthMethod.API_KEY.value,
        )
        db_session.add(app)
        await db_session.flush()
    auth_config = await _seed_auth_config(
        db_session,
        app_id=app_id,
        organization_id=fixed_test_org["id"],
        provider=AuthProvider.LEMMA.value,
    )
    operations_url = (
        f"/organizations/{fixed_test_org['id']}/connectors/"
        f"{auth_config.name}/operations"
    )

    await db_session.execute(
        delete(ConnectorOperation).where(
            ConnectorOperation.connector_id == app_id
        )
    )
    db_session.add_all(
        [
            ConnectorOperation(
                id=f"{app_id}:create_ticket",
                connector_id=app_id,
                name="create_ticket",
                provider_operation_name="create_ticket",
                display_name="Create Ticket",
                description="Create a support ticket for a customer issue.",
                input_schema={
                    "type": "object",
                    "properties": {"priority_code": {"type": "string"}},
                },
                output_schema={"type": "object"},
            ),
            ConnectorOperation(
                id=f"{app_id}:archive_ticket",
                connector_id=app_id,
                name="archive_ticket",
                provider_operation_name="archive_ticket",
                display_name="Archive Ticket",
                description="Archive an existing support ticket.",
                input_schema={
                    "type": "object",
                    "properties": {"internal_only_schema_term": {"type": "string"}},
                },
                output_schema={"type": "object"},
            ),
        ]
    )
    await db_session.commit()

    response = await authenticated_client.get(
        operations_url,
        params={"query": "customer issue"},
    )
    assert response.status_code == 200, response.text
    discovery = response.json()
    assert [item["name"] for item in discovery["items"]] == ["create_ticket"]

    response = await authenticated_client.get(
        operations_url,
        params={"query": "internal_only_schema_term"},
    )
    assert response.status_code == 200, response.text
    discovery = response.json()
    assert discovery["items"] == []
    assert discovery["returned_count"] == 0


@pytest.mark.asyncio
async def test_connector_operation_lookup_is_case_insensitive(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session,
):
    app_id = "test-operation-case-app"

    app = await db_session.get(Connector, app_id)
    if not app:
        app = _connector(
            app_id=app_id,
            title="Case Search App",
            description="App for operation lookup casing",
            provider=AuthProvider.COMPOSIO.value,
            auth_method=AuthMethod.OAUTH2.value,
        )
        db_session.add(app)
        await db_session.flush()
    auth_config = await _seed_auth_config(
        db_session,
        app_id=app_id,
        organization_id=fixed_test_org["id"],
        provider=AuthProvider.COMPOSIO.value,
    )
    operations_url = (
        f"/organizations/{fixed_test_org['id']}/connectors/"
        f"{auth_config.name}/operations"
    )

    await db_session.execute(
        delete(ConnectorOperation).where(
            ConnectorOperation.connector_id == app_id
        )
    )
    db_session.add(
        ConnectorOperation(
            id=f"{app_id}:EXCEL_CREATE_WORKBOOK",
            connector_id=app_id,
            provider=AuthProvider.COMPOSIO.value,
            name="EXCEL_CREATE_WORKBOOK",
            provider_operation_name="EXCEL_CREATE_WORKBOOK",
            display_name="Create Workbook",
            description="Create a new Excel workbook.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
    )
    await db_session.commit()

    response = await authenticated_client.get(
        f"{operations_url}/excel_create_workbook"
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "EXCEL_CREATE_WORKBOOK"

    response = await authenticated_client.post(
        f"{operations_url}/details",
        json={"operation_names": ["excel_create_workbook"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["name"] == "EXCEL_CREATE_WORKBOOK"

    response = await authenticated_client.get(
        operations_url,
        params={"query": "excel_create_workbook", "limit": 5},
    )
    assert response.status_code == 200, response.text
    discovery = response.json()
    assert discovery["items"][0]["name"] == "EXCEL_CREATE_WORKBOOK"
    assert discovery["items"][0]["relevance_score"] > 0


@pytest.mark.asyncio
async def test_connector_operation_requires_connected_user_account(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session,
):
    app_id = "test-operation-app-missing-account"
    app = await db_session.get(Connector, app_id)
    if not app:
        app = _connector(
            app_id=app_id,
            title="Missing Account App",
            description="App without connected account",
            provider=AuthProvider.LEMMA.value,
            auth_method=AuthMethod.API_KEY.value,
        )
        db_session.add(app)
        await db_session.flush()
    auth_config = await _seed_auth_config(
        db_session,
        app_id=app_id,
        organization_id=fixed_test_org["id"],
        provider=AuthProvider.LEMMA.value,
    )
    operations_url = (
        f"/organizations/{fixed_test_org['id']}/connectors/"
        f"{auth_config.name}/operations"
    )
    db_session.add(
        ConnectorOperation(
            id=f"{app_id}:test_op",
            connector_id=app_id,
            name="test_op",
            provider_operation_name="test_op",
            display_name="Test Op",
            description="Test Operation Desc",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
    )
    await db_session.commit()

    mock_execution_client = AsyncMock()
    mock_execution_client.list_operations.return_value = [
        SimpleNamespace(name="test_op")
    ]
    mock_execution_client.get_operation.return_value = SimpleNamespace(descriptor=None)
    mock_execution_client.execute_operation = AsyncMock(return_value={"ok": True})

    with patch(
        "app.modules.connectors.infrastructure.adapters.lemma_operation_gateway.create_lemma_execution_client",
        return_value=mock_execution_client,
    ):
        response = await authenticated_client.post(
            f"{operations_url}/test_op/execute",
            json={"payload": {"foo": "bar"}},
        )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "ACCOUNT_RESOLUTION_ERROR"


@pytest.mark.asyncio
async def test_connector_operation_returns_upstream_execution_error_details(
    authenticated_client: AsyncClient,
    fixed_test_user,
    fixed_test_org,
    db_session,
):
    app_id = "test-operation-app-upstream-error"

    app = await db_session.get(Connector, app_id)
    if not app:
        app = _connector(
            app_id=app_id,
            title="Test Operation App",
            description="Test App for upstream execution errors",
            provider=AuthProvider.LEMMA.value,
            auth_method=AuthMethod.API_KEY.value,
        )
        db_session.add(app)
        await db_session.flush()
    auth_config = await _seed_auth_config(
        db_session,
        app_id=app_id,
        organization_id=fixed_test_org["id"],
        provider=AuthProvider.LEMMA.value,
    )
    operations_url = (
        f"/organizations/{fixed_test_org['id']}/connectors/"
        f"{auth_config.name}/operations"
    )

    account_id = uuid4()
    db_session.add(
        Account(
            id=account_id,
            connector_id=app_id,
            user_id=fixed_test_user["id"],
            organization_id=fixed_test_org["id"],
            auth_config_id=auth_config.id,
            credentials={"api_key": "secret"},
        )
    )
    db_session.add(
        ConnectorOperation(
            id=f"{app_id}:send_message",
            connector_id=app_id,
            name="send_message",
            provider_operation_name="send_message",
            display_name="Send Message",
            description="Send Message",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
    )
    await db_session.commit()

    mock_execution_client = AsyncMock()
    mock_execution_client.list_operations.return_value = [
        SimpleNamespace(name="send_message")
    ]
    mock_execution_client.get_operation.return_value = SimpleNamespace(descriptor=None)
    mock_execution_client.execute_operation = AsyncMock(
        side_effect=FakeProviderOperationError(
            "API call failed with status code 200: {'ok': False, 'error': 'not_authed'}",
            status_code=200,
            details={"ok": False, "error": "not_authed"},
        )
    )

    with patch(
        "app.modules.connectors.infrastructure.adapters.lemma_operation_gateway.create_lemma_execution_client",
        return_value=mock_execution_client,
    ):
        response = await authenticated_client.post(
            f"{operations_url}/send_message/execute",
            json={
                "payload": {"channel": "#general", "text": "hi"},
                "account_id": str(account_id),
            },
        )

    assert response.status_code == 401, response.text
    assert response.json() == {
        "message": "API call failed with status code 200: {'ok': False, 'error': 'not_authed'}",
        "code": "OPERATION_EXECUTION_UNAUTHORIZED",
        "details": {
            "ok": False,
            "error": "not_authed",
            "upstream_message": "API call failed with status code 200: {'ok': False, 'error': 'not_authed'}",
        },
    }


@pytest.mark.asyncio
async def test_google_calendar_operation_uses_composio_account(
    authenticated_client: AsyncClient,
    fixed_test_user,
    fixed_test_org,
    db_session,
):
    app_id = "google_calendar"
    app = await db_session.get(Connector, app_id)
    if not app:
        app = _connector(
            app_id=app_id,
            title="Google Calendar",
            description="Google Calendar connector",
            provider=AuthProvider.COMPOSIO.value,
            auth_method=AuthMethod.OAUTH2.value,
        )
        db_session.add(app)
        await db_session.flush()
    auth_config = await _seed_auth_config(
        db_session,
        app_id=app_id,
        organization_id=fixed_test_org["id"],
        provider=AuthProvider.COMPOSIO.value,
    )
    operations_url = (
        f"/organizations/{fixed_test_org['id']}/connectors/"
        f"{auth_config.name}/operations"
    )

    db_session.add(
        ConnectorOperation(
            id=f"{app_id}:list_events",
            connector_id=app_id,
            provider=AuthProvider.COMPOSIO.value,
            name="list_events",
            provider_operation_name="list_events",
            display_name="List Events",
            description="List events from a calendar",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
    )

    account_id = uuid4()
    db_session.add(
        Account(
            id=account_id,
            connector_id=app_id,
            user_id=fixed_test_user["id"],
            organization_id=fixed_test_org["id"],
            auth_config_id=auth_config.id,
            credentials={
                "expires_at": None,
                "token_type": "Bearer",
                "access_token": "ya29...",
                "connection_id": "ca_nsKQ2C1X4Q4A",
            },
        )
    )
    await db_session.commit()

    with (
        patch.object(
            ComposioOperationGateway,
            "execute_operation",
            AsyncMock(return_value={"items": []}),
        ) as mock_execute,
        patch.object(
            ConnectorService,
            "get_account_credentials",
            AsyncMock(
                return_value=OAuthCredentials(
                    access_token="ya29...",
                    token_type="Bearer",
                    connection_id="ca_nsKQ2C1X4Q4A",
                )
            ),
        ) as mock_get_credentials,
        ):
            response = await authenticated_client.post(
                f"{operations_url}/LIST_EVENTS/execute",
                json={
                "payload": {"calendar_id": "primary"},
                "account_id": str(account_id),
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["result"] == {"items": []}
    mock_execute.assert_awaited_once()
    assert mock_execute.call_args.kwargs["third_party_credentials"]["connection_id"] == (
        "ca_nsKQ2C1X4Q4A"
    )
    mock_get_credentials.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.provider
async def test_google_calendar_operation_executes_against_real_composio_account(
    authenticated_client: AsyncClient,
    fixed_test_user,
    db_session,
):
    account_id = await _seed_real_google_calendar_account(
        db_session,
        user_id=fixed_test_user["id"],
    )

    response = await authenticated_client.get(
        "/connectors/connectors/google_calendar/operations",
        params={"query": "list events"},
    )
    assert response.status_code == 200, response.text
    operations = response.json()["items"]
    assert any(item["name"] == "list_events" for item in operations)

    response = await authenticated_client.post(
        "/connectors/connectors/google_calendar/operations/list_events/execute",
        json={
            "payload": {"calendar_id": "primary", "max_results": 1},
            "account_id": account_id,
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert isinstance(result, dict)
    assert isinstance(result.get("events"), list)
    assert isinstance(result.get("total_events"), int)
