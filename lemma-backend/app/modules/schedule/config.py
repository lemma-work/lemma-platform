"""Schedule module configuration."""

from pydantic import Field, SecretStr
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
    scheduler_internal_token: SecretStr | None = Field(
        default=None,
        description="Optional bearer token shared with the scheduler sidecar.",
    )


schedule_settings = ScheduleSettings()
