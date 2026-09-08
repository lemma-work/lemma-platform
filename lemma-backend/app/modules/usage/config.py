"""Usage module configuration.

Two halves that arrived from different directions and belong together: the
per-request accounting knobs from #478, and the deployment-wide system-spend
limits moved off `app/core/config.py` in the 0.8.0 config audit. Both are read
only by `mod:usage`.

Env var names are unchanged by the move: no settings class here sets
`env_prefix`, so pydantic-settings derives each name from the field identically
on whichever class holds it.
"""

from typing import Literal

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

    # Per-request accounting.
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

    # Deployment-wide system-spend limits. None means unlimited.
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
    usage_unpriced_limit_policy: Literal["allow", "refuse"] = Field(
        default="allow",
        description=(
            "What to do when a monetary limit applies but this deployment has "
            "no enforceable price for the model. `allow` runs the request and "
            "meters it, priced with whatever rate the catalog holds, so the "
            "limit still binds approximately. `refuse` rejects it, which "
            "a deployment billing somebody else for the usage should set, "
            "because a limit it cannot measure is not a limit. This "
            "covers the rate card only -- a request whose *shape* has no price "
            "(a priority tier, `extra_body`, 1h cache writes) asks the provider "
            "for billable work the adapter never sees, and is refused whatever "
            "this is set to. "
            "The default is `allow` because refusing is almost always the "
            "wrong answer for the deployment that hits this: a model served "
            "through an OpenAI-compatible gateway is never enforceable -- the "
            "catalog resolves the vendor's list price, not what the gateway "
            "charges -- so `refuse` turned a spend cap into a total outage for "
            "anyone self-hosting behind vLLM, LiteLLM, OpenRouter or a proxy. "
            "Either way the deployment is told at startup which models cannot "
            "back a limit; state prices in LEMMA_SYSTEM_MODEL_METADATA_JSON to "
            "make the limit bind again."
        ),
    )


usage_settings = UsageSettings()
