"""Usage tracking and optional spend-limit configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path
from app.modules.usage.domain.ports import UsageLimitValues


class UsageSettings(BaseSettings):
    """OSS defaults are unlimited; deployments opt into monetary limits.

    Usage events are recorded regardless of these values. A non-null limit (or
    an installed billing-plan adapter) enables reservation/admission and thus
    requires registered pricing and model budget metadata.
    """

    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    usage_default_org_monthly_cost_limit_usd: float | None = Field(
        default=None,
        ge=0,
    )
    usage_default_user_weekly_cost_limit_usd: float | None = Field(
        default=None,
        ge=0,
    )
    usage_default_user_monthly_cost_limit_usd: float | None = Field(
        default=None,
        ge=0,
    )

    def default_limit_values(self) -> UsageLimitValues:
        return UsageLimitValues(
            org_monthly_limit_usd=self.usage_default_org_monthly_cost_limit_usd,
            user_weekly_limit_usd=self.usage_default_user_weekly_cost_limit_usd,
            user_monthly_limit_usd=self.usage_default_user_monthly_cost_limit_usd,
            user_limit_scope="organization",
        )


usage_settings = UsageSettings()
