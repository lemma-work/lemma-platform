from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.models.connector_operation import (
    ConnectorOperation,
)
from app.modules.connectors.infrastructure.models.connector_trigger import (
    ConnectorTrigger,
)
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig


@pytest.fixture
async def test_connector(db_session: AsyncSession):
    app = Connector(
        id="google_calendar",
        title="Google Calendar",
        description="Calendar connector",
        kinds=[{"kind": "package", "auth_scheme": "OAUTH2"}],
        is_active=True,
    )
    db_session.add(app)
    db_session.add(
        ConnectorOperation(
            id="google_calendar:list_events",
            connector_id="google_calendar",
            name="list_events",
            provider_operation_name="LIST_EVENTS",
            display_name="List Events",
            description="List calendar events",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
    )
    await db_session.commit()
    await db_session.refresh(app)
    return app


async def _seed_trigger_auth_config(
    db_session: AsyncSession, *, app_id: str, organization_id, kind: str
) -> AuthConfig:
    auth_config = AuthConfig(
        organization_id=organization_id,
        connector_id=app_id,
        kind=kind,
        config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
        name=f"{app_id}-{uuid4().hex[:8]}",
    )
    db_session.add(auth_config)
    await db_session.flush()
    return auth_config


@pytest.mark.asyncio
async def test_list_connectors(authenticated_client: AsyncClient, test_connector):
    response = await authenticated_client.get("/connectors")
    assert response.status_code == 200, response.text
    data = response.json()
    assert "items" in data
    assert len(data["items"]) >= 1
    found = False
    for item in data["items"]:
        if item["id"] == test_connector.id:
            found = True
            assert item["title"] == test_connector.title

    assert found


@pytest.mark.asyncio
async def test_get_connector(authenticated_client: AsyncClient, test_connector):
    response = await authenticated_client.get(
        f"/connectors/{test_connector.id}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == test_connector.id
    assert data["operations"]["list_events"]["description"] == "List calendar events"


@pytest.mark.asyncio
async def test_get_connector_not_found_returns_domain_payload(
    authenticated_client: AsyncClient,
):
    response = await authenticated_client.get("/connectors/missing-app")
    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == "CONNECTOR_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,expected_config_field",
    [
        (ConnectorKind.COMPOSIO.value, "composio_field"),
        (ConnectorKind.PACKAGE.value, "lemma_field"),
    ],
)
async def test_triggers_filtered_by_auth_config_kind(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session: AsyncSession,
    kind: str,
    expected_config_field: str,
):
    """An install sees only the triggers its own kind emits."""
    app_id = f"trigger-filter-{kind}"
    db_session.add(
        Connector(
            id=app_id,
            title="Trigger Filter App",
            description="App carrying both LEMMA and COMPOSIO triggers",
            kinds=[
                {"kind": "package", "auth_scheme": "OAUTH2"},
                {
                    "kind": "composio",
                    "auth_scheme": "OAUTH2",
                    "toolkit_slug": "filter",
                },
            ],
            is_active=True,
        )
    )
    db_session.add_all(
        [
            ConnectorTrigger(
                id=f"{app_id}:lemma:new_message",
                connector_id=app_id,
                kind="package",
                event_type="new_message",
                description="LEMMA new message",
                config_schema={
                    "type": "object",
                    "properties": {"lemma_field": {"type": "string"}},
                },
                payload_schema={"type": "object"},
            ),
            ConnectorTrigger(
                id=f"{app_id}:composio:new_message",
                connector_id=app_id,
                kind="composio",
                event_type="new_message",
                description="COMPOSIO new message",
                config_schema={
                    "type": "object",
                    "properties": {"composio_field": {"type": "string"}},
                },
                payload_schema={"type": "object"},
            ),
        ]
    )
    auth_config = await _seed_trigger_auth_config(
        db_session,
        app_id=app_id,
        organization_id=fixed_test_org["id"],
        kind=kind,
    )
    await db_session.commit()

    triggers_url = (
        f"/organizations/{fixed_test_org['id']}/connectors/"
        f"{auth_config.name}/triggers"
    )

    response = await authenticated_client.get(triggers_url)
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["kind"] == kind
    # The list endpoint returns the lightweight trigger summary, which omits the
    # heavy config_schema/payload_schema (see TriggerSummaryResponse). The full
    # config_schema is asserted on the detail endpoint below.

    detail = await authenticated_client.get(f"{triggers_url}/new_message")
    assert detail.status_code == 200, detail.text
    detail_data = detail.json()
    assert detail_data["kind"] == kind
    assert expected_config_field in detail_data["config_schema"]["properties"]
