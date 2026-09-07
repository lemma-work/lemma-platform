"""Workspace module configuration.

Everything about provisioning and reaching a sandbox. The names are the
module's own -- `WORKSPACE_*`, plus `FUNCTION_*` for the function runtime and
`E2B_*` for that provider's credentials.
"""

from typing import Literal, Optional

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


class WorkspaceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Which fabric sandboxes are rented from ---------------------------
    # Docker is the default because it needs no credentials and no network.
    provider: Literal["docker", "e2b", "lemma_local"] = Field(
        default="docker",
        validation_alias=AliasChoices("WORKSPACE_PROVIDER"),
        description="Sandbox provider used by the workspace module",
    )

    # --- Images and profiles ----------------------------------------------
    workspace_image: str = Field(
        default="lemma-workspace:dev",
        validation_alias=AliasChoices("WORKSPACE_IMAGE"),
        description="Container image backing workspace sandboxes",
    )
    function_image: str = Field(
        default="lemma-function:dev",
        validation_alias=AliasChoices("FUNCTION_IMAGE"),
        description="Container image backing function runtime sandboxes",
    )
    workspace_profile_name: str = Field(
        default="workspace-python-v1",
        validation_alias=AliasChoices("WORKSPACE_PROFILE_NAME"),
        description="Immutable workspace profile name",
    )
    workspace_profile_digest: str = Field(
        # Bumped when the workspace image changes, so a sandbox built from the
        # previous one is replaced rather than reused. Last moved when the
        # GitHub CLI was added to the image.
        default=f"sha256:{'3' * 64}",
        pattern=r"^sha256:[0-9a-f]{64}$",
        validation_alias=AliasChoices("WORKSPACE_PROFILE_DIGEST"),
        description="Immutable workspace profile digest",
    )
    function_profile_name: str = Field(
        default="function-python-v1",
        validation_alias=AliasChoices("FUNCTION_PROFILE_NAME"),
        description="Immutable function profile name",
    )
    function_profile_digest: str = Field(
        default=f"sha256:{'2' * 64}",
        pattern=r"^sha256:[0-9a-f]{64}$",
        validation_alias=AliasChoices("FUNCTION_PROFILE_DIGEST"),
        description="Immutable function profile digest",
    )

    # --- Credentials and access -------------------------------------------
    # Signs the per-container token the in-sandbox runtime accepts. Nothing can
    # be provisioned without it.
    runtime_credential_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("WORKSPACE_RUNTIME_CREDENTIAL_KEY"),
        description="At least 32 bytes; signs in-sandbox runtime credentials",
    )
    port_access_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("WORKSPACE_PORT_ACCESS_URL"),
        description=(
            "Public base URL for signed sandbox port access. Defaults to "
            "api_url; set when the proxy is reached on a different origin."
        ),
    )

    # --- Reclamation -------------------------------------------------------
    process_max_lifetime_seconds: int = Field(
        default=3600,
        validation_alias=AliasChoices("WORKSPACE_PROCESS_MAX_LIFETIME_SECONDS"),
        description=(
            "How long a process started by exec_command may run before the "
            "runtime terminates it. Separate from a tool call's wait window: a "
            "build is allowed to outlive the call that started it, but not to "
            "outlive the conversation and pin the sandbox forever. Generous on "
            "purpose — this is a leak guard, not a command budget."
        ),
    )
    idle_release_seconds: int = Field(
        default=900,
        validation_alias=AliasChoices("WORKSPACE_IDLE_RELEASE_SECONDS"),
        description=(
            "Release a sandbox unused for this long. Releasing stops compute "
            "and keeps the disk, so being wrong costs a slower next tool call "
            "rather than lost work. 0 disables the sweep."
        ),
    )
    sweep_cron: str = Field(
        default="2-59/5 * * * *",
        validation_alias=AliasChoices("WORKSPACE_SWEEP_CRON"),
        description=(
            "How often idle release and orphan reclaim run. Orphan reclaim is "
            "what stops a container or paid sandbox outliving the row that "
            "owned it, so this is a cost control. Offset off the round minute "
            "on purpose -- see test_cron_schedule_spread."
        ),
    )

    # --- Docker ------------------------------------------------------------
    docker_socket_path: str = Field(
        default="/var/run/docker.sock",
        validation_alias=AliasChoices("WORKSPACE_DOCKER_SOCKET_PATH"),
        description="Docker Engine unix socket used to provision sandboxes",
    )
    docker_private_network: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("WORKSPACE_DOCKER_PRIVATE_NETWORK"),
        description="Docker network to attach sandboxes to instead of publishing ports",
    )
    docker_allow_mutable_images: bool = Field(
        default=False,
        validation_alias=AliasChoices("WORKSPACE_DOCKER_ALLOW_MUTABLE_IMAGES"),
        description=(
            "Allow sandbox images pinned by tag rather than sha256 digest. "
            "Development only: a moving tag means the image that ran is not "
            "the image that was reviewed."
        ),
    )
    # Lets a sandbox reach the backend on the host. Without it a function
    # sandbox cannot fetch its artifact and a workspace cannot call back, so
    # provisioning succeeds and everything after it fails.
    add_host_gateway: bool = Field(
        default=False,
        validation_alias=AliasChoices("WORKSPACE_ADD_HOST_GATEWAY"),
        description="Map the host gateway into sandboxes under host_alias",
    )
    host_alias: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("WORKSPACE_HOST_ALIAS"),
        description="Hostname sandboxes use to reach the host running the backend",
    )

    # --- E2B ---------------------------------------------------------------
    e2b_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias=AliasChoices("E2B_API_KEY"),
        description="E2B API key",
    )
    e2b_workspace_template: str = Field(
        default="lemma-workspace",
        validation_alias=AliasChoices("E2B_WORKSPACE_TEMPLATE"),
        description="E2B template backing workspace sandboxes",
    )
    e2b_function_template: str = Field(
        default="lemma-function",
        validation_alias=AliasChoices("E2B_FUNCTION_TEMPLATE"),
        description="E2B template backing function runtime sandboxes",
    )
    e2b_domain: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("E2B_DOMAIN"),
        description="E2B API domain override",
    )
    e2b_metadata_namespace: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("E2B_METADATA_NAMESPACE"),
        description=(
            "Namespace for every metadata key the E2B provider writes and "
            "queries. A provider is blind to sandboxes labelled by another "
            "namespace, which is what makes it safe to point one deployment at "
            "an account that also holds another's workspaces.\n\n"
            "This is a safety boundary, not a preference, and it deliberately "
            "has no shared default. The orphan sweep destroys any provider "
            "object it can identify as ours but cannot find a sandbox row for, "
            "and another deployment's sandboxes have no row here -- so sharing "
            "this value across deployments means each one deletes the other's "
            "live workspaces. It did: dev and prod held separate API keys for "
            "one E2B team, both fell back to the same default, and each "
            "destroyed the other's sandboxes every five minutes.\n\n"
            "Unset, it is derived from ENVIRONMENT by "
            "``provider_factory.resolve_metadata_namespace``, which refuses to "
            "derive one for `local` or `testing` because every developer and "
            "every CI run would share it."
        ),
    )

    # --- Lemma Desktop's native bridge into its VZ/WSL guest ---------------
    # Written by lemma-stack/config/render.py and locald's native host pack.
    local_runtime_cli: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("WORKSPACE_LOCAL_RUNTIME_CLI"),
        description="Executable bridging to the Lemma Desktop guest runtime",
    )
    local_callback_required: bool = Field(
        default=False,
        validation_alias=AliasChoices("WORKSPACE_LOCAL_CALLBACK_REQUIRED"),
        description="Require the guest to reach the backend before reporting ready",
    )
    local_callback_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("WORKSPACE_LOCAL_CALLBACK_URL"),
        description="URL the guest calls back on",
    )

    # Moved from `app/core/config.py`: the callback URLs the sandbox uses to
    # reach back into the platform, read only by this module.
    workspace_callback_api_url: Optional[str] = Field(
        default=None,
        description=(
            "URL workspace sandboxes use to reach this API (e.g. http://backend:8000 "
            "when sandboxes share a container network). No hostname inference "
            "or rewriting is performed when absent."
        ),
    )
    workspace_callback_auth_url: Optional[str] = Field(
        default=None,
        description=(
            "Explicit auth frontend URL reachable from workspace sandboxes; "
            "no hostname rewriting is performed when absent."
        ),
    )
    workspace_callback_frontend_url: Optional[str] = Field(
        default=None,
        description=(
            "Explicit frontend origin reachable from workspace sandboxes; "
            "no hostname rewriting is performed when absent."
        ),
    )


workspace_settings = WorkspaceSettings()
