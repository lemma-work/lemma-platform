"""Settings only `mod:decisions` reads."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path


class DecisionsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    typesafe_api_key: SecretStr | None = Field(
        default=None,
        description=(
            "Key for Typesafe's System One endpoint. Set, it is the second rung "
            "of every decision; unset, decisions go from rules straight to the "
            "system model. The same name the frontend's call router reads."
        ),
    )
    typesafe_model: str = Field(
        default="jev-1.13.0",
        description=(
            "System One model version. A version id, never `jev-latest`: the "
            "alias moves when a release ships, and every recorded decision names "
            "the version that answered it."
        ),
    )
    typesafe_base_url: str = Field(
        default="https://api.typesafe.ai/v1",
        description=(
            "Base URL of a System One compatible endpoint. `/systemone` is appended."
        ),
    )
    typesafe_timeout_seconds: float = Field(
        default=8.0,
        gt=0,
        description=(
            "Ceiling on one System One request for ambient and bulk decisions."
        ),
    )
    typesafe_interactive_timeout_seconds: float = Field(
        default=3.0,
        gt=0,
        description=(
            "Ceiling on one System One request for interactive decisions, where "
            "a person is waiting and the next rung is the better use of time."
        ),
    )
    typesafe_requests_per_second: int = Field(
        default=40,
        ge=1,
        description=(
            "Requests per second this deployment sends System One, across every "
            "pod and process. One key is shared by all of them, and the "
            "provider documents its limit per key."
        ),
    )
    decisions_system_one_enabled: bool = Field(
        default=True,
        description=(
            "Operator switch for the System One rung. Off leaves rules and the "
            "system model, whatever key is configured."
        ),
    )
    decision_model: str | None = Field(
        default=None,
        description=(
            "Model name for the model rung, resolved on the system profile. "
            "Unset uses the profile default, then the workspace's model."
        ),
    )
    decision_evidence_ttl_days: int = Field(
        default=30,
        ge=1,
        description=(
            "Days a decision keeps the input view it was asked about, so a "
            "person can review and correct it. Examples made from a correction "
            "keep their copy for as long as the decider exists."
        ),
    )
    decisions_max_rows: int = Field(
        default=500,
        ge=1,
        description="Rows one batch request may decide.",
    )
    decisions_row_concurrency: int = Field(
        default=8,
        ge=1,
        description="Rows of one batch decided at the same time.",
    )


decisions_settings = DecisionsSettings()
