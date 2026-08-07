"""How configuration selects and builds a provider.

These are cheap checks guarding expensive mistakes: a provider that reads the
wrong environment variable, or a Docker sandbox built without the host gateway,
both provision successfully and then fail at the first thing a user does.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.modules.workspace.providers.base import ProviderStorageKind


def test_desktop_env_var_names_are_still_honoured(monkeypatch) -> None:
    """Lemma Desktop already sets the AGENTBOX_LOCAL_* names.

    If this module only read WORKSPACE_LOCAL_*, the cutover would leave every
    machine on the current desktop build unable to reach its guest -- a silent
    break with no error until a workspace is opened.
    """
    monkeypatch.setenv("AGENTBOX_LOCAL_RUNTIME_CLI", "/opt/lemma/bin/bridge")
    monkeypatch.setenv("AGENTBOX_LOCAL_CALLBACK_REQUIRED", "true")
    monkeypatch.setenv("AGENTBOX_LOCAL_CALLBACK_URL", "http://127.0.0.1:8710")
    monkeypatch.delenv("WORKSPACE_LOCAL_RUNTIME_CLI", raising=False)

    settings = Settings()

    assert settings.workspace_local_runtime_cli == "/opt/lemma/bin/bridge"
    assert settings.workspace_local_callback_required is True
    assert settings.workspace_local_callback_url == "http://127.0.0.1:8710"


def test_the_new_names_win_when_both_are_set(monkeypatch) -> None:
    monkeypatch.setenv("AGENTBOX_LOCAL_RUNTIME_CLI", "/old/bridge")
    monkeypatch.setenv("WORKSPACE_LOCAL_RUNTIME_CLI", "/new/bridge")
    assert Settings().workspace_local_runtime_cli == "/new/bridge"


def test_the_docker_provider_carries_the_host_gateway(monkeypatch) -> None:
    """Without it a sandbox cannot reach the backend, so a function never
    fetches its artifact and a workspace never calls back -- both long after
    provisioning has reported success."""
    from app.core.config import settings
    from app.modules.workspace.services.provider_factory import build_provider

    monkeypatch.setattr(settings, "workspace_provider", "docker")
    monkeypatch.setattr(settings, "workspace_runtime_credential_key", "k" * 32)
    monkeypatch.setattr(settings, "agentbox_add_host_gateway", True)
    monkeypatch.setattr(settings, "agentbox_host_alias", "host.lemma.internal")

    provider = build_provider()

    assert provider.name == "docker"
    assert provider._config.add_host_gateway is True
    assert provider._config.host_alias == "host.lemma.internal"
    assert provider.storage_kind is ProviderStorageKind.VOLUME


def test_each_provider_declares_where_storage_lives() -> None:
    """The service branches on this, and getting it wrong loses user files:
    a sandbox-native provider whose sandbox is replaced takes the disk with
    it."""
    from app.modules.workspace.providers.docker import DockerSandboxProvider
    from app.modules.workspace.providers.e2b import E2BSandboxProvider
    from app.modules.workspace.providers.lemma_local import (
        LemmaLocalSandboxProvider,
    )

    assert DockerSandboxProvider.storage_kind is ProviderStorageKind.VOLUME
    assert E2BSandboxProvider.storage_kind is ProviderStorageKind.SANDBOX_NATIVE
    assert (
        LemmaLocalSandboxProvider.storage_kind is ProviderStorageKind.SANDBOX_NATIVE
    )


def test_an_unknown_provider_is_refused_at_startup(monkeypatch) -> None:
    from app.modules.workspace.services.provider_factory import build_provider

    with pytest.raises(RuntimeError, match="unsupported"):
        build_provider("kubernetes")


def test_e2b_requires_its_key_rather_than_failing_later(monkeypatch) -> None:
    from app.core.config import settings
    from app.modules.workspace.services.provider_factory import build_provider

    monkeypatch.setattr(settings, "e2b_api_key", None)
    with pytest.raises(RuntimeError, match="E2B_API_KEY"):
        build_provider("e2b")


def test_lemma_local_requires_its_bridge_rather_than_failing_later(
    monkeypatch,
) -> None:
    from app.core.config import settings
    from app.modules.workspace.services.provider_factory import build_provider

    monkeypatch.setattr(settings, "workspace_local_runtime_cli", None)
    with pytest.raises(RuntimeError, match="RUNTIME_CLI"):
        build_provider("lemma_local")
