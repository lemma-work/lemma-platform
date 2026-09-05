"""Function module configuration.

These thirteen settings moved out of `app/core/config.py`, which was 1,756 lines
and 220 fields. Every one of them is read only inside `mod:function` -- measured,
not assumed -- which made this the cleanest cluster in the codebase to move
first.

**No `AliasChoices` here, and none needed.** No settings class in this codebase
sets `env_prefix`, so pydantic-settings derives each env var from the field name
identically on both classes: `FUNCTION_API_DEADLINE_SECONDS` reaches this class
exactly as it reached `Settings`. The `ca2d8cad1` precedent used aliases because
those fields were *renamed* on the way (`agentbox_workspace_image` ->
`workspace_image`); a move that keeps the name needs nothing.
"""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


class FunctionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    function_builder_executable: str = Field(
        default="uv",
        description="Executable used only while prebuilding function dependencies",
    )
    function_builder_python_platform: Optional[str] = Field(
        default=None,
        description="uv Linux wheel target matching the function runtime image",
    )
    function_builder_digest: str = Field(
        default="local-uv-builder-1",
        description="Immutable builder identity included in function revision hashes",
    )
    function_session_token_cache_ttl_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
    )
    function_session_token_cache_max_entries: int = Field(
        default=4096,
        ge=1,
        le=100_000,
    )
    function_runtime_endpoint_reuse_seconds: int = Field(
        default=60,
        ge=5,
        le=240,
        description=(
            "How far ahead a function-runtime lease is requested beyond the "
            "current invocation's own needs, so a busy pod reuses one lease "
            "instead of paying a control-plane call per invocation. "
            "The sandbox runtime treats a lease as activity and keeps the sandbox alive "
            "for the horizon it grants, so this must stay well below "
            "WORKSPACE_IDLE_RELEASE_SECONDS: otherwise a single invocation "
            "keeps a pod's sandbox billing long after the last function ran. "
            "Function execution is the activity that should keep a sandbox "
            "warm - never the mere existence of a cached endpoint. "
            "Read the deployed idle release rather than the field default when "
            "tuning this - production runs 180, not 900, so the usable ceiling "
            "is far below this field's own maximum. The effective value is "
            "clamped against it at construction; see endpoint_reuse_seconds."
        ),
    )
    function_runtime_endpoint_cache_max_entries: int = Field(
        default=4096,
        ge=1,
        le=100_000,
    )
    function_api_deadline_seconds: int = Field(default=120, ge=1, le=3600)
    function_job_deadline_seconds: int = Field(default=600, ge=1, le=3_000)
    function_run_retention_days: int = Field(
        default=30,
        ge=1,
        description=(
            "How long a terminal function run is kept. Runs carry their whole "
            "input and output payload plus captured logs, so this table grows "
            "faster in bytes than in rows, and until this existed nothing ever "
            "removed one. Longer than the event-delivery window because these "
            "rows are user-visible run history, not delivery receipts."
        ),
    )
    function_run_retention_batch_size: int = Field(
        default=1_000,
        ge=1,
        le=10_000,
        description="Rows removed per transaction by the function-run sweep.",
    )
    function_run_retention_budget_seconds: float = Field(
        default=45.0,
        ge=0.0,
        description=(
            "Wall-clock budget for one function-run retention sweep. Deletes "
            "run in batches until drained or the budget is spent, so a backlog "
            "clears over successive runs rather than never. Zero disables the "
            "sweep entirely."
        ),
    )
    function_runtime_gateway_url: Optional[str] = Field(
        default=None,
        description="Backend URL reachable from function sandboxes",
    )


function_settings = FunctionSettings()
