"""Workspace module configuration.

Everything about provisioning and reaching a sandbox. The names are the
module's own -- `WORKSPACE_*`, plus `FUNCTION_*` for the function runtime and
`E2B_*` for that provider's credentials. The `AGENTBOX_*` spellings these
fields used to accept are gone; `_reject_renamed_env_vars` below turns a
leftover into a refusal to boot rather than a silent fallback to the default.
"""

import os
from typing import Literal, Optional

from dotenv import dotenv_values
from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path

# Every environment variable this module used to read, and what replaced it.
# Nothing in the repo writes the old names any more, so one that is still set
# is hand-authored -- a deployment config or a `.env` -- and silently ignoring
# it would mean booting on a default the operator did not choose. Remove this
# once no environment on any release still carries them.
RENAMED_ENV_VARS = {
    "AGENTBOX_WORKSPACE_IMAGE": "WORKSPACE_IMAGE",
    "AGENTBOX_FUNCTION_IMAGE": "FUNCTION_IMAGE",
    "AGENTBOX_WORKSPACE_PROFILE_NAME": "WORKSPACE_PROFILE_NAME",
    "AGENTBOX_WORKSPACE_PROFILE_DIGEST": "WORKSPACE_PROFILE_DIGEST",
    "AGENTBOX_FUNCTION_PROFILE_NAME": "FUNCTION_PROFILE_NAME",
    "AGENTBOX_FUNCTION_PROFILE_DIGEST": "FUNCTION_PROFILE_DIGEST",
    "AGENTBOX_RUNTIME_CREDENTIAL_KEY": "WORKSPACE_RUNTIME_CREDENTIAL_KEY",
    "AGENTBOX_WORKSPACE_IDLE_SECONDS": "WORKSPACE_IDLE_RELEASE_SECONDS",
    "AGENTBOX_DOCKER_SOCKET_PATH": "WORKSPACE_DOCKER_SOCKET_PATH",
    "AGENTBOX_DOCKER_PRIVATE_NETWORK": "WORKSPACE_DOCKER_PRIVATE_NETWORK",
    "AGENTBOX_DOCKER_ALLOW_MUTABLE_IMAGES": "WORKSPACE_DOCKER_ALLOW_MUTABLE_IMAGES",
    "AGENTBOX_ADD_HOST_GATEWAY": "WORKSPACE_ADD_HOST_GATEWAY",
    "AGENTBOX_HOST_ALIAS": "WORKSPACE_HOST_ALIAS",
    "AGENTBOX_LOCAL_RUNTIME_CLI": "WORKSPACE_LOCAL_RUNTIME_CLI",
    "AGENTBOX_LOCAL_CALLBACK_REQUIRED": "WORKSPACE_LOCAL_CALLBACK_REQUIRED",
    "AGENTBOX_LOCAL_CALLBACK_URL": "WORKSPACE_LOCAL_CALLBACK_URL",
}


def _configured_names() -> set[str]:
    """Every environment name this process would read a setting from.

    The process environment is not enough. Pydantic reads the dotenv file too,
    and a hand-edited `lemma-backend/.env` is the likeliest place a renamed
    name survives, since every writer in this repo moved in the same change.
    """

    names = {key.upper() for key in os.environ}
    path = dotenv_path()
    if path:
        names.update(key.upper() for key in dotenv_values(path))
    return names


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
        default="*/5 * * * *",
        validation_alias=AliasChoices("WORKSPACE_SWEEP_CRON"),
        description=(
            "How often idle release and orphan reclaim run. Orphan reclaim is "
            "what stops a container or paid sandbox outliving the row that "
            "owned it, so this is a cost control."
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

    @model_validator(mode="after")
    def _reject_renamed_env_vars(self) -> "WorkspaceSettings":
        """Refuse to start on a environment variable that no longer configures
        anything.

        Pydantic only reads names it declares, so a leftover `AGENTBOX_*` is
        not an ignored extra -- it is invisible, and the field quietly takes
        its default. That default is rarely harmless: `WORKSPACE_IMAGE` falls
        back to a tag `make init` builds locally, so a stale name resolves to a
        real but months-old image rather than failing to pull, and
        `WORKSPACE_ADD_HOST_GATEWAY` falls back to False, where provisioning
        succeeds and every call the sandbox makes afterwards does not.

        Only the names this module used to read are checked. The sandbox's own
        variables are deliberately absent: a workspace container legitimately
        has them set, and inheriting one must not stop the backend.
        """

        configured = _configured_names()
        stale = sorted(
            f"{old} (now {new})"
            for old, new in RENAMED_ENV_VARS.items()
            if old in configured and new not in configured
        )
        if stale:
            raise ValueError(
                "These environment variables were renamed and no longer "
                "configure anything; set the new name instead: "
                + ", ".join(stale)
            )
        return self


workspace_settings = WorkspaceSettings()
