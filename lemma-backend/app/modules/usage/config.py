"""Settings for batched usage persistence."""

from decimal import Decimal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


class UsageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=dotenv_path(), extra="ignore")

    usage_request_output_ceiling: int = Field(
        default=8192,
        ge=1,
        description="Default output-token ceiling for each limited model request",
    )
    usage_batch_requests: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Completed provider requests per usage checkpoint",
    )
    usage_batch_seconds: float = Field(
        default=30,
        gt=0,
        le=300,
        description="Maximum seconds between active usage checkpoints",
    )
    usage_budget_chunk_usd: Decimal = Field(
        default=Decimal("1"),
        gt=0,
        description="USD allocation target and headroom for large request bounds",
    )
    usage_allocation_timeout_seconds: int = Field(
        default=120,
        ge=30,
        description="Seconds without a checkpoint before an allocation is classified uncertain",
    )
    usage_limit_warn_fraction: float = Field(
        default=0.8,
        ge=0,
        le=1,
        description="Confirmed fraction of a limit that emits its once-per-window warning",
    )


usage_settings = UsageSettings()
