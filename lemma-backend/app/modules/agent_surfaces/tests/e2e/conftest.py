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
sandbox_reachable_backend = e2e_fixtures.sandbox_reachable_backend
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


@pytest.fixture(autouse=True)
def configured_email_domain(monkeypatch):
    """Model a deployment that has actually set up inbound email.

    ``resend_inbound_domain`` has no default on purpose: a fallback would mint
    addresses on a domain nobody owns, which bounce on the way out and match no
    surface on the way back. Tests that provision a Resend surface therefore
    have to configure it, exactly as an operator does.
    """
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.asur.work")


@pytest_asyncio.fixture(autouse=True)
async def hermetic_telegram_api(fake_telegram, monkeypatch):
    """Keep every surface E2E call inside the fake Telegram boundary.

    Surface creation now synchronizes commands and the chat menu even in tests
    that exercise only routing or onboarding. Those tests previously had no
    reason to request ``fake_telegram`` and consequently reached Telegram's
    public API with test tokens. Making the provider boundary suite-wide keeps
    all create/update paths deterministic while preserving the per-test fake's
    request recording.
    """
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(
        surface_settings,
        "telegram_bot_token",
        "surface-e2e-system-telegram",
    )
    yield


@pytest_asyncio.fixture(scope="session")
async def fake_composio_server():
    server = FakeComposioServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest_asyncio.fixture(scope="session")
async def worker(e2e_settings, sandbox_reachable_backend, fake_composio_server, request):
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
    "test_pod",
    "test_redis_url",
    "sandbox_reachable_backend",
    "worker",
]
