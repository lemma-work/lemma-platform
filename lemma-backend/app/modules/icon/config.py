"""Icon module configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


class IconSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    icon_upload_max_bytes: int = Field(default=5 * 1024 * 1024)
    icon_max_dimension_pixels: int = Field(default=4096)
    icon_max_total_pixels: int = Field(default=16_777_216)


icon_settings = IconSettings()
