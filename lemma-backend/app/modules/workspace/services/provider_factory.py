"""Builds the configured sandbox provider.

Separated from the service because it is configuration, not behaviour: this is
the only place that knows a provider name maps to a class, and keeping it here
means the service imports one function instead of every provider -- including
the ones a given deployment will never install.
"""

from __future__ import annotations

from app.modules.workspace.config import workspace_settings

def build_provider(name: str | None = None):
    """Construct the configured sandbox provider.

    The choice is a config value rather than a code path so a deployment can
    move between fabrics without a different build, and so exactly one place
    has to be read to know which one is live.
    """
    chosen = name or workspace_settings.provider
    if chosen == "e2b":
        return _build_e2b_provider()
    if chosen == "lemma_local":
        return _build_lemma_local_provider()
    if chosen == "docker":
        return _build_docker_provider()
    raise RuntimeError(f"unsupported workspace provider: {chosen}")


def _build_lemma_local_provider():
    from app.modules.workspace.providers.docker import RuntimeCredentialSigner
    from app.modules.workspace.providers.lemma_local import (
        LemmaLocalProviderConfig,
        LemmaLocalSandboxProvider,
    )

    executable = workspace_settings.local_runtime_cli
    if not executable:
        raise RuntimeError(
            "WORKSPACE_LOCAL_RUNTIME_CLI is required when workspace_provider "
            "is lemma_local"
        )
    key = workspace_settings.runtime_credential_key
    if not key:
        raise RuntimeError(
            "WORKSPACE_RUNTIME_CREDENTIAL_KEY is required to provision sandboxes"
        )
    return LemmaLocalSandboxProvider(
        LemmaLocalProviderConfig(
            executable=executable,
            callback_required=workspace_settings.local_callback_required,
            callback_url=workspace_settings.local_callback_url,
        ),
        RuntimeCredentialSigner(key=key.encode()),
    )


def _build_docker_provider():
    from app.modules.workspace.providers.docker import (
        DockerProviderConfig,
        DockerSandboxProvider,
        RuntimeCredentialSigner,
    )
    from app.modules.workspace.providers.docker_engine import DockerEngineClient

    key = workspace_settings.runtime_credential_key
    if not key:
        raise RuntimeError(
            "WORKSPACE_RUNTIME_CREDENTIAL_KEY is required to provision sandboxes"
        )
    return DockerSandboxProvider(
        DockerEngineClient(socket_path=workspace_settings.docker_socket_path),
        DockerProviderConfig(
            allow_mutable_images=workspace_settings.docker_allow_mutable_images,
            # Without the host gateway a sandbox cannot reach the backend, so
            # a function never fetches its artifact and a workspace never
            # calls back -- both fail well after provisioning looks healthy.
            add_host_gateway=workspace_settings.add_host_gateway,
            host_alias=workspace_settings.host_alias,
            private_network=workspace_settings.docker_private_network,
        ),
        RuntimeCredentialSigner(key=key.encode()),
    )


def _build_e2b_provider():
    from app.core.config import reveal_secret
    from app.modules.workspace.providers.e2b import (
        E2BProviderConfig,
        E2BSandboxProvider,
    )

    api_key = reveal_secret(workspace_settings.e2b_api_key)
    if not api_key:
        raise RuntimeError("E2B_API_KEY is required when workspace_provider is e2b")
    return E2BSandboxProvider(
        E2BProviderConfig(
            api_key=api_key,
            workspace_template=workspace_settings.e2b_workspace_template,
            function_template=workspace_settings.e2b_function_template,
            domain=workspace_settings.e2b_domain,
            # Carried explicitly. The provider defaults this, and defaulting it
            # here too is what let a test run share production's namespace --
            # which the orphan sweep reads as "these are mine and have no row",
            # against a database where nothing does.
            metadata_namespace=workspace_settings.e2b_metadata_namespace,
        )
    )
