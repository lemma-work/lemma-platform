"""Builds the configured sandbox provider.

Separated from the service because it is configuration, not behaviour: this is
the only place that knows a provider name maps to a class, and keeping it here
means the service imports one function instead of every provider -- including
the ones a given deployment will never install.
"""

from __future__ import annotations

from app.core.config import settings

def build_provider(name: str | None = None):
    """Construct the configured sandbox provider.

    The choice is a config value rather than a code path so a deployment can
    move between fabrics without a different build, and so exactly one place
    has to be read to know which one is live.
    """
    chosen = name or settings.workspace_provider
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

    executable = settings.workspace_local_runtime_cli
    if not executable:
        raise RuntimeError(
            "WORKSPACE_LOCAL_RUNTIME_CLI is required when workspace_provider "
            "is lemma_local"
        )
    key = settings.workspace_runtime_credential_key
    if not key:
        raise RuntimeError(
            "WORKSPACE_RUNTIME_CREDENTIAL_KEY is required to provision sandboxes"
        )
    return LemmaLocalSandboxProvider(
        LemmaLocalProviderConfig(
            executable=executable,
            callback_required=settings.workspace_local_callback_required,
            callback_url=settings.workspace_local_callback_url,
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

    key = settings.workspace_runtime_credential_key
    if not key:
        raise RuntimeError(
            "WORKSPACE_RUNTIME_CREDENTIAL_KEY is required to provision sandboxes"
        )
    return DockerSandboxProvider(
        DockerEngineClient(socket_path=settings.agentbox_docker_socket_path),
        DockerProviderConfig(
            allow_mutable_images=settings.agentbox_docker_allow_mutable_images,
            # Without the host gateway a sandbox cannot reach the backend, so
            # a function never fetches its artifact and a workspace never
            # calls back -- both fail well after provisioning looks healthy.
            add_host_gateway=settings.agentbox_add_host_gateway,
            host_alias=settings.agentbox_host_alias,
            private_network=settings.agentbox_docker_private_network,
        ),
        RuntimeCredentialSigner(key=key.encode()),
    )


def _build_e2b_provider():
    from app.core.config import reveal_secret
    from app.modules.workspace.providers.e2b import (
        E2BProviderConfig,
        E2BSandboxProvider,
    )

    api_key = reveal_secret(settings.e2b_api_key)
    if not api_key:
        raise RuntimeError("E2B_API_KEY is required when workspace_provider is e2b")
    return E2BSandboxProvider(
        E2BProviderConfig(
            api_key=api_key,
            workspace_template=settings.e2b_workspace_template,
            function_template=settings.e2b_function_template,
            domain=settings.e2b_domain,
        )
    )
