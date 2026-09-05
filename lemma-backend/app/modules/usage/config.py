"""Usage module configuration.

The deployment-wide system-spend limits, moved off `app/core/config.py`.
None means unlimited.

Env var names are unchanged: no settings class here sets `env_prefix`, so
pydantic-settings derives each name from the field identically on whichever
class holds it.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


class UsageSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    usage_org_monthly_limit_usd: float | None = Field(
        default=None,
        description=(
            "Deployment-wide monthly system-spend limit per organization, in "
            "USD. None means unlimited; work that would exceed it is refused "
            "with USAGE_LIMIT_EXCEEDED. See PS-OPS-012."
        ),
    )
    usage_user_weekly_limit_usd: float | None = Field(
        default=None,
        description="Deployment-wide weekly system-spend limit per user, in USD.",
    )
    usage_user_monthly_limit_usd: float | None = Field(
        default=None,
        description="Deployment-wide monthly system-spend limit per user, in USD.",
    )


usage_settings = UsageSettings()
