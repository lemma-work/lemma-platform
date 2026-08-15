"""Schedule module configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


class ScheduleSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    scheduler_api_url: str = Field(
        default="http://localhost:8711", description="Scheduler API URL"
    )
    schedule_max_consecutive_failures: int = Field(
        default=5,
        description="Deactivate a schedule after this many consecutive execution errors.",
    )
    schedule_minimum_interval_minutes: int = Field(
        default=15,
        ge=1,
        description="Minimum interval between recurring TIME schedule executions.",
    )
    schedule_run_retention_days: int = Field(
        default=90,
        ge=1,
        description=(
            "Delete schedule runs that reached a terminal outcome more than this "
            "many days ago. The ledger is append-only and had no retention at "
            "all: 81k rows growing ~1,000 a day, which every index on the table "
            "pays for. Must stay comfortably longer than a live failure streak "
            "could span, or pruning could shorten a streak the breaker is still "
            "counting. Env: ``SCHEDULE_RUN_RETENTION_DAYS``."
        ),
    )
    schedule_run_retention_batch_size: int = Field(
        default=1000,
        ge=1,
        description=(
            "Rows deleted per transaction by the retention sweep. Env: "
            "``SCHEDULE_RUN_RETENTION_BATCH_SIZE``."
        ),
    )
    schedule_run_retention_budget_seconds: float = Field(
        default=30.0,
        ge=0,
        description=(
            "Wall-clock ceiling for one retention sweep, checked between "
            "batches so a run stops at a batch boundary and the next tick "
            "resumes. Zero disables the drain loop and deletes one batch per "
            "run. Env: ``SCHEDULE_RUN_RETENTION_BUDGET_SECONDS``."
        ),
    )


schedule_settings = ScheduleSettings()
