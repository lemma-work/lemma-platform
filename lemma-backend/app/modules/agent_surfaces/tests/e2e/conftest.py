from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import status

from app.modules.agent_surfaces.tests.e2e.helpers import (
    fake_composio_email,
    fake_gmail,
    fake_outlook,
    fake_resend,
    fake_slack,
    fake_speech_provider,
    fake_teams,
    fake_telegram,
    fake_whatsapp,
    message_store,
)
from app.modules.agent.tests.e2e.system_lemma_helpers import (
    skip_unless_system_lemma,
    system_lemma_api_key,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    FakeComposioServer,
)
from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e.worker_process import production_worker_process

# Re-export shared E2E fixtures so this module can run with --confcutdir.
test_network = e2e_fixtures.test_network
postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
db_manager = e2e_fixtures.db_manager
test_app = e2e_fixtures.test_app
async_client = e2e_fixtures.async_client
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org
db_session = e2e_fixtures.db_session
scenario = e2e_fixtures.scenario


@pytest.fixture(autouse=True)
def public_surface_api_url(monkeypatch):
    """Advertise the HTTPS ingress boundary used by webhook E2E journeys.

    The ASGI client remains in-process and fake providers remain local.  This
    setting only models the externally reachable URL that providers require
    when a surface is registered.  Native polling/socket tests deliberately
    override it with localhost inside the individual scenario.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "api_url", "https://surface-e2e.test")


@pytest_asyncio.fixture(scope="session")
async def fake_composio_server():
    server = FakeComposioServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest_asyncio.fixture(scope="session")
async def worker(e2e_settings, fake_composio_server, request):
    """Surface shard's production worker with a hermetic Composio transport.

    Default e2e mode uses the deterministic FunctionModel token source. When
    ``E2E_LLM_MODE=real`` is set, the existing system:lemma helper gates on the
    configured LEMMA_OPENAI_* credentials. The local Composio API preserves the
    real SDK/gateway boundary for Gmail and Outlook without live credentials.
    """
    from app.core.config import settings

    skip_unless_system_lemma()
    key = system_lemma_api_key()
    previous_setting = settings.lemma_openai_api_key
    if key:
        settings.lemma_openai_api_key = key
    try:
        async with production_worker_process(
            e2e_settings,
            log_prefix="lemma_system_lemma_surface_worker",
            extra_env={
                "COMPOSIO_API_KEY": "test",
                "COMPOSIO_BASE_URL": fake_composio_server.base_url,
                "MICROSOFT_BOT_APP_ID": "teams-app-id",
                "MICROSOFT_BOT_APP_PASSWORD": "teams-app-secret",
            },
            readiness_markers=(
                "`HandleAgentRunEvent` waiting for messages",
                "`HandleScheduleEvents` waiting for messages",
                "`HandleSurfaceWebhook` waiting for messages",
            ),
        ) as process:
            yield process
            if request.session.testsfailed:
                # The worker is a subprocess, so pytest cannot otherwise attach
                # its exception/log context to a failed journey.  Emit only on
                # failure to keep successful shard output concise.
                print("\n--- surface production worker tail ---")
                print(process.read_log_tail())
    finally:
        settings.lemma_openai_api_key = previous_setting


@pytest_asyncio.fixture
async def test_pod(authenticated_client, fixed_test_org):
    org_id = fixed_test_org["id"]
    payload = {
        "name": f"Surface Test Pod {uuid4()}",
        "slug": f"surface-test-pod-{uuid4()}",
        "type": "ASSISTANT",
        "organization_id": org_id,
    }
    response = await authenticated_client.post(
        "/pods",
        json=payload,
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


__all__ = [
    "authenticated_client",
    "async_client",
    "db_manager",
    "db_session",
    "e2e_settings",
    "fake_composio_email",
    "fake_composio_server",
    "fake_gmail",
    "fake_outlook",
    "fake_resend",
    "fake_slack",
    "fake_speech_provider",
    "fake_teams",
    "fake_telegram",
    "fake_whatsapp",
    "fixed_test_org",
    "fixed_test_user",
    "message_store",
    "postgres_container",
    "redis_container",
    "scenario",
    "supertokens_container",
    "test_app",
    "test_database_url",
    "test_network",
    "test_pod",
    "test_redis_url",
    "worker",
]
