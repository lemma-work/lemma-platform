"""Workflow module configuration.

Four settings only `mod:workflow` and its one reader in `mod:agent_surfaces`
use, moved off `app/core/config.py`.

Env var names are unchanged: no settings class here sets `env_prefix`, so
pydantic-settings derives each name from the field identically on whichever
class holds it.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


class WorkflowSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    workflow_wait_retention_days: int = Field(
        default=30,
        ge=1,
        description=(
            "How long a finished machine wait (FUNCTION/AGENT/TIME) is kept. "
            "HUMAN waits are excluded from the sweep entirely at any age -- "
            "they record who approved what, which is not scaffolding."
        ),
    )
    workflow_wait_retention_batch_size: int = Field(default=1_000, ge=1, le=10_000)
    workflow_wait_retention_budget_seconds: float = Field(
        default=45.0,
        ge=0.0,
        description=(
            "Wall-clock budget for one workflow-wait sweep. Zero disables it."
        ),
    )


workflow_settings = WorkflowSettings()
