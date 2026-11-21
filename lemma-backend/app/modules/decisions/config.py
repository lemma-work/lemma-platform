"""Decisions module configuration: which provider answers, and its limits."""

from typing import Literal

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

    decision_provider: Literal["gemini", "model", "typesafe"] = Field(
        default="gemini",
        description=(
            "Which provider answers decisions. `gemini` asks Google's "
            "Decisions API and needs LEMMA_GOOGLE_API_KEY; `model` asks the "
            "deployment's language model (the system model, else the "
            "workspace's); `typesafe` asks Typesafe System One and needs "
            "TYPESAFE_API_KEY. A provider chosen without its key falls back "
            "to `model`."
        ),
    )
    decision_model: str | None = Field(
        default=None,
        description=(
            "The model the `model` provider asks, by public name. Empty uses "
            "the system profile's default model."
        ),
    )
    lemma_google_api_key: SecretStr | None = Field(
        default=None,
        description=(
            "Gemini API key for the `gemini` provider -- the same one the "
            "Google model profile takes. Google serves decisions on the Gemini "
            "API only, which refuses service accounts, so this is needed even "
            "where models run on Vertex AI."
        ),
    )
    gemini_decision_base_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta",
        description="Gemini API base URL; requests go to `{base}/decisions`.",
    )
    gemini_decision_model: str = Field(
        default="gemini-decision-1", description="The Gemini decision model to ask."
    )
    gemini_decision_price_per_million_input_tokens_usd: float | None = Field(
        default=0.10,
        ge=0,
        description=(
            "What Google charges per million input tokens for decisions (output "
            "is free). Unset, decisions are recorded unpriced and follow "
            "USAGE_UNPRICED_LIMIT_POLICY."
        ),
    )
    typesafe_api_key: SecretStr | None = Field(
        default=None, description="API key for Typesafe System One."
    )
    typesafe_base_url: str = Field(
        default="https://api.typesafe.ai/v1",
        description="Typesafe API base URL; requests go to `{base}/systemone`.",
    )
    typesafe_model: str = Field(
        default="jev-latest", description="The System One model to ask."
    )
    typesafe_price_per_million_input_tokens_usd: float | None = Field(
        default=None,
        ge=0,
        description=(
            "What System One charges per million input tokens. Set it so "
            "decisions count toward spend limits; unset, they are recorded "
            "unpriced and follow USAGE_UNPRICED_LIMIT_POLICY."
        ),
    )
    decision_interactive_timeout_seconds: float = Field(
        default=8.0,
        gt=0,
        le=28,
        description="How long an interactive decision may take, end to end.",
    )
    decision_background_timeout_seconds: float = Field(
        default=25.0,
        gt=0,
        le=28,
        description=(
            "How long a background decision may take, end to end. Kept under "
            "the SDKs' 30-second client timeout so callers see this server's "
            "answer rather than their own."
        ),
    )
    decision_rate_limit_per_minute: int = Field(
        default=600,
        ge=0,
        description=(
            "Decisions one organization may ask per minute, counted separately "
            "for interactive and background decisions; 0 for no limit."
        ),
    )


decisions_settings = DecisionsSettings()
