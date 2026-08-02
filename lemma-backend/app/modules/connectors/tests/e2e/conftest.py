from uuid import uuid4

import pytest
import pytest_asyncio

from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.test_support.e2e import fixtures as e2e_fixtures

# Re-export shared e2e fixture stack so module tests run with --confcutdir.
test_network = e2e_fixtures.test_network
postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
worker = e2e_fixtures.worker
db_manager = e2e_fixtures.db_manager
test_app = e2e_fixtures.test_app
db_session = e2e_fixtures.db_session
async_client = e2e_fixtures.async_client
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org
scenario = e2e_fixtures.scenario

pytestmark = pytest.mark.e2e


@pytest.fixture
async def connector_test_connector(db_session):
    app_id = f"connector-app-{uuid4().hex[:8]}"
    app = Connector(
        id=app_id,
        title="Connector Test App",
        description="Connector test connector",
        kinds=[{"kind": "package", "auth_scheme": "OAUTH2"}],
        is_active=True,
    )
    db_session.add(app)
    await db_session.commit()
    await db_session.refresh(app)
    return app


@pytest_asyncio.fixture(scope="function")
async def connector_test_pod(authenticated_client, fixed_test_org):
    payload = {
        "organization_id": fixed_test_org["id"],
        "name": f"Connector Pod {uuid4()}",
        "type": "HYBRID",
        "description": "Connector e2e pod",
    }
    response = await authenticated_client.post("/pods", json=payload)
    assert response.status_code == 201, response.text
    return response.json()
