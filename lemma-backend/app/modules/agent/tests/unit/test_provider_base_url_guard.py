"""Where a member may point this deployment's model-provider requests.

A provider's ``base_url`` is chosen by whoever configures it, and the backend
then sends requests there with the provider's key attached. Local mode lets it
be loopback so a model server on the developer's own machine works. That is
only sound while the developer is the only one who can configure anything: a
shared Desktop installation is still local mode, and any member who joined over
the network could otherwise aim the backend at this Mac's Ollama, a dev server,
or Lemma's own API and database ports.
"""

from __future__ import annotations

import pytest

from app.core.exposure import exposure_settings

from app.modules.agent.services import runtime_provider_discovery as discovery

pytestmark = pytest.mark.unit


@pytest.fixture
def local_install(monkeypatch):
    settings = discovery.settings
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(exposure_settings, "installation_shared", False)
    monkeypatch.setattr(settings, "api_url", "http://app.lemma.localhost:52414")
    monkeypatch.setattr(settings, "frontend_url", "http://app.lemma.localhost:52413")
    monkeypatch.setattr(
        settings, "auth_frontend_url", "http://app.lemma.localhost:52413/auth"
    )
    monkeypatch.setattr(settings, "supertokens_core_url", "http://127.0.0.1:53567")
    monkeypatch.setattr(
        settings,
        "database_url",
        "postgresql+asyncpg://postgres:x@127.0.0.1:55432/lemma",
    )
    monkeypatch.setattr(settings, "redis_url", "redis://:x@127.0.0.1:56379")
    return settings


async def test_a_local_model_server_is_allowed_on_an_unshared_install(local_install):
    await discovery._validate_public_base_url("http://127.0.0.1:11434/v1/models")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:52414/models",  # the API itself, bearer key attached
        "http://127.0.0.1:52413/models",  # the web app
        "http://127.0.0.1:53567/models",  # SuperTokens core
        "http://127.0.0.1:55432/models",  # Postgres
        "http://127.0.0.1:56379/models",  # Redis
    ],
)
async def test_lemmas_own_ports_are_never_a_provider(local_install, url):
    with pytest.raises(ValueError):
        await discovery._validate_public_base_url(url)


async def test_loopback_is_refused_while_the_install_is_shared(
    local_install, monkeypatch
):
    monkeypatch.setattr(exposure_settings, "installation_shared", True)
    with pytest.raises(ValueError):
        await discovery._validate_public_base_url("http://127.0.0.1:11434/v1/models")


async def test_private_addresses_stay_refused_in_local_mode(local_install):
    with pytest.raises(ValueError):
        await discovery._validate_public_base_url("http://192.168.1.5:11434/v1")
