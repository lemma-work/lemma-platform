"""Apps module upload and archive configuration."""

from typing import Self

from pydantic import Field, model_validator
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

    # Release retention. See app.core.retention for why there are three knobs:
    # keep_last is the floor that keeps rollback possible for a dormant app,
    # keep_days keeps work being iterated on, and max_keep is the ceiling that
    # bounds a burst of deploys. The live release is exempt from all of them.
    app_release_retention_enabled: bool = Field(default=True)
    app_release_keep_last: int = Field(default=10, ge=1)
    app_release_keep_days: int = Field(default=30, ge=0)
    app_release_max_keep: int = Field(default=20, ge=1)
    app_release_retention_cron: str = Field(default="20 4 * * *")
    app_release_retention_batch: int = Field(
        default=200,
        ge=1,
        description=(
            "Apps fetched per round trip by the release sweep. This is the PAGE "
            "size, not the tick size: the sweep pages until the candidate set is "
            "drained, so this bounds one query rather than deciding which apps "
            "ever get swept. Env: ``APP_RELEASE_RETENTION_BATCH``."
        ),
    )
    app_release_retention_budget_seconds: float = Field(
        default=60.0,
        ge=0.0,
        description=(
            "Wall-clock budget for one release sweep. ZERO MEANS UNLIMITED -- the "
            "opposite of the schedule-run drain, where zero stops after one "
            "batch. Draining is the point here, so the default must not be to "
            "stop early. Env: ``APP_RELEASE_RETENTION_BUDGET_SECONDS``."
        ),
    )

    # Moved from `app/core/config.py`: read only by this module's asset resolver.
    app_branding_enabled: bool = Field(
        default=True,
        description=(
            "Show the host-owned 'Remix on Lemma' attribution on public app "
            "entrypoints. Enabled by default in OSS and cloud; cloud billing may "
            "remove it for entitled organizations."
        ),
    )

    @model_validator(mode="after")
    def validate_retention_bounds(self) -> Self:
        if self.app_release_max_keep < self.app_release_keep_last:
            raise ValueError("APP_RELEASE_MAX_KEEP must be >= APP_RELEASE_KEEP_LAST")
        return self


apps_settings = AppsSettings()
