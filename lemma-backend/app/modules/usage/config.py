"""Settings for per-request usage accounting."""

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
    usage_limit_warn_fraction: float = Field(
        default=0.8,
        ge=0,
        le=1,
        description="Confirmed fraction of a limit that emits its once-per-window warning",
    )


usage_settings = UsageSettings()
