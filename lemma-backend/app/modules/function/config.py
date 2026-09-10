"""Function module configuration.

Two classes, both owned by this module.

`FunctionSettings` holds the thirteen settings that moved out of
`app/core/config.py`, which was 1,756 lines and 220 fields. Every one is read
only inside `mod:function` -- measured, not assumed -- which made this the
cleanest cluster to move first.

`FunctionRevisionSettings` holds the retention knobs for immutable function
builds. It arrived here from the other direction, with the version-history work
in #346, and is kept as its own class because it carries a cross-field
validator: `max_keep` below `keep_last` is a retention policy that can never be
satisfied, and it should refuse to start rather than sweep to a floor nobody
asked for.

**No `AliasChoices` on either.** No settings class in this codebase sets
`env_prefix`, so pydantic-settings derives each env var from the field name
identically on whichever class holds it: `FUNCTION_API_DEADLINE_SECONDS` reaches
this module exactly as it reached `Settings`. The `ca2d8cad1` precedent used
aliases because those fields were *renamed* on the way; a move that keeps the
name needs nothing.
"""

from typing import Optional, Self

from pydantic import Field, model_validator
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


class FunctionRevisionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Revision retention. See app.core.retention for the three-knob rule; the
    # live revision is exempt, as is any revision with a run still in flight.
    function_revision_retention_enabled: bool = Field(default=True)
    function_revision_keep_last: int = Field(default=10, ge=1)
    function_revision_keep_days: int = Field(default=30, ge=0)
    function_revision_max_keep: int = Field(default=20, ge=1)
    function_revision_retention_cron: str = Field(default="40 4 * * *")
    function_revision_retention_batch: int = Field(
        default=200,
        ge=1,
        description=(
            "Functions fetched per round trip by the revision sweep. The PAGE "
            "size, not the tick size: the sweep pages until the candidate set "
            "drains. Env: ``FUNCTION_REVISION_RETENTION_BATCH``."
        ),
    )
    function_revision_retention_budget_seconds: float = Field(
        default=60.0,
        ge=0.0,
        description=(
            "Wall-clock budget for one revision sweep. ZERO MEANS UNLIMITED -- "
            "the opposite of FUNCTION_RUN_RETENTION_BUDGET_SECONDS, where zero "
            "disables the sweep. Draining is the point here. "
            "Env: ``FUNCTION_REVISION_RETENTION_BUDGET_SECONDS``."
        ),
    )

    @model_validator(mode="after")
    def validate_retention_bounds(self) -> Self:
        if self.function_revision_max_keep < self.function_revision_keep_last:
            raise ValueError(
                "FUNCTION_REVISION_MAX_KEEP must be >= FUNCTION_REVISION_KEEP_LAST"
            )
        return self


function_settings = FunctionSettings()
revision_settings = FunctionRevisionSettings()
