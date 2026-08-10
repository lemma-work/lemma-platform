"""How configuration selects and builds a provider.

These are cheap checks guarding expensive mistakes: a provider that reads the
wrong environment variable, or a Docker sandbox built without the host gateway,
both provision successfully and then fail at the first thing a user does.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.workspace.config import WorkspaceSettings, workspace_settings
from app.modules.workspace.providers.base import ProviderStorageKind


def test_the_module_reads_its_own_env_var_names(monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_LOCAL_RUNTIME_CLI", "/opt/lemma/bin/bridge")
    monkeypatch.setenv("WORKSPACE_LOCAL_CALLBACK_REQUIRED", "true")
    monkeypatch.setenv("WORKSPACE_LOCAL_CALLBACK_URL", "http://127.0.0.1:8710")

    resolved = WorkspaceSettings()

    assert resolved.local_runtime_cli == "/opt/lemma/bin/bridge"
    assert resolved.local_callback_required is True
    assert resolved.local_callback_url == "http://127.0.0.1:8710"


def test_the_e2b_surface_is_exactly_four_settings() -> None:
    """`E2B_*_BUILD_ID` is a CI repository variable, not a backend setting.

    The workflows pin the exact template build their runs exercise with it.
    Reading it here would be reasonable and is not what happens, so an
    operator who sets it in a deployment environment is configuring nothing.
    Anyone adding a fifth E2B setting should have to decide, deliberately,
    whether `docs/configuration.md` is still true.
    """
    declared = {
        (
            field.validation_alias.choices[0]
            if field.validation_alias
            else name.upper()
        )
        for name, field in WorkspaceSettings.model_fields.items()
    }

    assert {name for name in declared if name.startswith("E2B_")} == {
        "E2B_API_KEY",
        "E2B_WORKSPACE_TEMPLATE",
        "E2B_FUNCTION_TEMPLATE",
        "E2B_DOMAIN",
    }


def test_the_docker_provider_carries_the_host_gateway(monkeypatch) -> None:
    """Without it a sandbox cannot reach the backend, so a function never
    fetches its artifact and a workspace never calls back -- both long after
    provisioning has reported success."""
    from app.modules.workspace.services.provider_factory import build_provider

    monkeypatch.setattr(workspace_settings, "provider", "docker")
    monkeypatch.setattr(workspace_settings, "runtime_credential_key", "k" * 32)
    monkeypatch.setattr(workspace_settings, "add_host_gateway", True)
    monkeypatch.setattr(workspace_settings, "host_alias", "host.lemma.internal")

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
    from app.modules.workspace.services.provider_factory import build_provider

    monkeypatch.setattr(workspace_settings, "e2b_api_key", None)
    with pytest.raises(RuntimeError, match="E2B_API_KEY"):
        build_provider("e2b")


def test_lemma_local_requires_its_bridge_rather_than_failing_later(
    monkeypatch,
) -> None:
    from app.modules.workspace.services.provider_factory import build_provider

    monkeypatch.setattr(workspace_settings, "local_runtime_cli", None)
    with pytest.raises(RuntimeError, match="RUNTIME_CLI"):
        build_provider("lemma_local")
