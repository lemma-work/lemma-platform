"""Retention configuration for immutable function builds."""

from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.settings_env import dotenv_path


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


revision_settings = FunctionRevisionSettings()
