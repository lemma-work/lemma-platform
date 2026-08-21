"""Builds the configured sandbox provider.

Separated from the service because it is configuration, not behaviour: this is
the only place that knows a provider name maps to a class, and keeping it here
means the service imports one function instead of every provider -- including
the ones a given deployment will never install.
"""

from __future__ import annotations

from app.core.log.log import get_logger
from app.modules.workspace.config import workspace_settings

logger = get_logger(__name__)

# Environments whose name is shared by many deployments at once. Every
# developer's machine reports `local`, and every CI run reports `testing`, so a
# namespace derived from either would be identical across all of them -- which
# is the same collision the derivation exists to prevent, just between
# colleagues instead of between dev and prod. These must say who they are.
_AMBIGUOUS_ENVIRONMENTS = frozenset({"local", "testing"})


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
            metadata_namespace=resolve_metadata_namespace(),
        )
    )


def resolve_metadata_namespace(
    configured: str | None = None, environment: str | None = None
) -> str:
    """Which metadata namespace this deployment writes and queries E2B under.

    This is the boundary that decides whether two deployments can see each
    other's sandboxes, and it used to have a shared default. Both `lemma-dev`
    and `lemma-prod` held their own `E2B_API_KEY`, but the two keys resolved to
    one E2B team, and neither set a namespace -- so each backend enumerated the
    other's sandboxes, found no row for them in its own database, and destroyed
    them as orphans. One user's workspace was wiped five times inside a single
    twenty-minute conversation, each kill landing seconds after the other
    environment rebuilt it.

    So there is no shared default any more. An explicit `E2B_METADATA_NAMESPACE`
    always wins; otherwise the namespace is derived from `ENVIRONMENT`, which
    already distinguishes the deployments that collided.

    `local` and `testing` are refused rather than derived. Deriving would give
    every developer's machine and every CI run the same namespace, which
    rebuilds the same collision at a smaller scale -- and those are precisely
    the deployments that pair a throwaway database with a real E2B account, the
    combination that turns a sweep into a deletion. A warning would not do:
    warnings in local development are not read, and the cost of missing one is
    somebody else's work.
    """

    from app.core.config import settings

    explicit = (
        configured
        if configured is not None
        else workspace_settings.e2b_metadata_namespace
    )
    if explicit:
        return explicit

    resolved_environment = environment or settings.environment
    if resolved_environment in _AMBIGUOUS_ENVIRONMENTS:
        raise RuntimeError(
            "E2B_METADATA_NAMESPACE must be set explicitly when ENVIRONMENT is "
            f"{resolved_environment!r}. It is the only thing keeping this "
            "deployment from seeing sandboxes that belong to another one: the "
            "orphan sweep destroys any sandbox it can identify as this "
            "platform's but cannot find a row for, and on E2B destroying a "
            f"sandbox destroys the user's files. Every {resolved_environment} "
            "deployment reports the same environment name, so a derived value "
            "would not tell them apart. Pick something unique, for example "
            f"'lemma-{resolved_environment}-<your-name>'."
        )

    namespace = f"lemma-{resolved_environment}"
    logger.info(
        "workspace.provider_factory.metadata_namespace_derived",
        namespace=namespace,
        environment=resolved_environment,
    )
    return namespace
