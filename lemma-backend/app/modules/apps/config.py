"""Apps module upload and archive configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


class AppsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_source_archive_max_bytes: int = Field(default=100 * 1024 * 1024)
    app_dist_archive_max_bytes: int = Field(default=100 * 1024 * 1024)
    app_bundle_upload_max_bytes: int = Field(default=200 * 1024 * 1024)
    app_archive_max_entries: int = Field(default=10_000)
    app_archive_max_uncompressed_bytes: int = Field(default=400 * 1024 * 1024)
    app_archive_max_compression_ratio: int = Field(default=200)


apps_settings = AppsSettings()
