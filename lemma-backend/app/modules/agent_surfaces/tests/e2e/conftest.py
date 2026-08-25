from __future__ import annotations

from uuid import uuid4

import os

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


# Set before anything imports settings, so every reader sees it — including the
# worker, which serves these tests from its own task and picked up an attribute
# patched onto one Settings instance too late to matter. The fixture below says
# why this suite is entitled to it.
os.environ.setdefault("CONNECTOR_ALLOW_PRIVATE_NETWORK_TARGETS", "true")


@pytest.fixture(autouse=True)
def reachable_fake_providers(monkeypatch):
    """Model a self-hosted deployment, because the fakes are on loopback.

    Every surface here points `api_base_url` at a fake server on 127.0.0.1, and
    that address is now checked before the client dials it — an unguarded
    `api_base_url` is a straight path from a stored credential to the cloud
    metadata service, so it is validated at the point of use like any other
    tenant-supplied URL.

    Production refuses loopback, correctly. Self-hosting is the supported way
    to say "my network is the target", which is exactly the situation these
    tests are in. The refusal itself is asserted in
    `agent_surfaces/tests/unit/test_api_base_is_guarded.py`, including that the
    metadata service stays refused even with this open.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "connector_allow_private_network_targets", True)


@pytest.fixture(autouse=True)
def configured_email_domain(monkeypatch):
    """Model a deployment that has actually set up inbound email.

    ``resend_inbound_domain`` has no default on purpose: a fallback would mint
    addresses on a domain nobody owns, which bounce on the way out and match no
    surface on the way back. Tests that provision a Resend surface therefore
    have to configure it, exactly as an operator does.

    The domain only. Provisioning is gated on ``email_is_configured()``, which
    wants a key as well, so a pod created under this fixture has no mailbox —
    which is the premise most of this shard is written against and the reason
    ``UNDELIVERABLE`` is an assertion here rather than a failure. Use
    :func:`pod_with_a_mailbox` for the other deployment.
    """
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.asur.work")


@pytest_asyncio.fixture
async def pod_with_a_mailbox(authenticated_client, fixed_test_org, monkeypatch):
    """A pod on a deployment where email is *fully* configured.

    The distinction is load-bearing and was invisible for a while. The autouse
    fixture above sets the inbound domain and no API key, and CI sets neither —
    so `email_is_configured()` is false everywhere in this shard, no pod or
    agent is ever given a mailbox at creation, and a bug that needs one to
    exist cannot reproduce. A collision between the pod assistant's surface and
    a plain "connect email" request held for every pod on a configured
    deployment while this suite stayed green.

    Both settings go on *before* the pod is created, because what they change is
    what pod creation does.
    """
    from app.core.config import settings as core_settings
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.asur.work")
    monkeypatch.setattr(core_settings, "resend_api_key", "re_test")

    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Mailbox Pod {uuid4()}",
            "slug": f"mailbox-pod-{uuid4()}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


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
async def worker(
    e2e_settings, sandbox_reachable_backend, fake_composio_server, request
):
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
