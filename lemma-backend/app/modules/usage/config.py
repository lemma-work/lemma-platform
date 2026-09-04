"""Usage module configuration.

The spend *limits* themselves stay in core config: a deployment sets them
alongside everything else an operator configures, and admission reads them
through `UsageLimitPort` rather than from here. What lives here is the module's
own behaviour around those limits.
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

    usage_limit_warn_fraction: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of a spend window at which the people it applies to are "
            "warned, once per window, before work starts being refused. 1.0 "
            "warns only at the point of refusal; 0 disables the warning "
            "entirely. See PS-OPS-010."
        ),
    )


usage_settings = UsageSettings()
