"""Calls module configuration: which voice engine talks, and its providers."""

from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_env import dotenv_path

VoiceEngineName = Literal["gemini_live", "openai_realtime", "cascade"]


class CallsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    call_voice_engine: VoiceEngineName = Field(
        default="gemini_live",
        description=(
            "The engine a call talks through when the caller names none: "
            "`gemini_live` (one native-audio session), `openai_realtime` "
            "(OpenAI's realtime model), or `cascade` (Deepgram speech-to-text, "
            "a Lemma model, Deepgram text-to-speech)."
        ),
    )
    gemini_api_key: SecretStr | None = Field(
        default=None, description="Gemini API key for the `gemini_live` engine."
    )
    gemini_vertex_project: str | None = Field(
        default=None,
        description=(
            "A Google Cloud project to run `gemini_live` in through Vertex AI, "
            "as this server's own service account (Workload Identity on GKE). "
            "Takes the place of `GEMINI_API_KEY`; the account needs "
            "`roles/aiplatform.user`."
        ),
    )
    gemini_vertex_location: str = Field(
        default="us-central1",
        description="The Vertex AI region a `gemini_live` call opens in, or `global`.",
    )
    gemini_live_model: str = Field(
        default="gemini-3.8-live",
        description="The Gemini Live model a `gemini_live` call opens.",
    )
    gemini_live_voice: str | None = Field(
        default=None,
        description="A Gemini prebuilt voice name; empty keeps the model's default.",
    )
    openai_api_key: SecretStr | None = Field(
        default=None,
        description="OpenAI API key for the `openai_realtime` engine.",
    )
    openai_realtime_model: str = Field(
        default="gpt-realtime-2.1-mini",
        description=(
            "The OpenAI realtime model an `openai_realtime` call opens. The mini "
            "model by default: the voice only talks, the agents behind it think."
        ),
    )
    openai_realtime_voice: str = Field(
        default="marin", description="The OpenAI realtime voice."
    )
    # Shared with the agent module's speech tools, which read the same variable.
    deepgram_api_key: SecretStr | None = Field(
        default=None,
        description="Deepgram API key for the `cascade` engine's speech.",
    )
    cascade_stt_model: str = Field(
        default="flux-general-en",
        description=(
            "Deepgram Flux model that transcribes the caller and decides when "
            "their turn ended. `flux-general-multi` for other languages."
        ),
    )
    cascade_eager_eot_threshold: float | None = Field(
        default=0.5,
        ge=0.3,
        le=0.9,
        description=(
            "Flux's eager end-of-turn threshold: the talker starts drafting on "
            "an eager end of turn and drops the draft if the caller resumes. "
            "Empty waits for the confirmed end of turn."
        ),
    )
    cascade_tts_voice: str = Field(
        default="aura-2-thalia-en", description="Deepgram Aura voice for `cascade`."
    )
    cascade_talker_model: str | None = Field(
        default=None,
        description=(
            "The model that speaks on a `cascade` call, by public name. Empty "
            "uses the system profile's default. Pick a fast one: it answers "
            "every turn, and the agents behind it do the slow work."
        ),
    )
    cascade_talker_reasoning_effort: (
        Literal["none", "minimal", "low", "medium", "high"] | None
    ) = Field(
        default="none",
        description=(
            "Reasoning effort asked of the cascade talker. Off by default: every "
            "token it thinks is silence on the call. A model that cannot turn "
            "reasoning off (some are thinking-only) is a poor talker; pick another "
            "rather than raising this."
        ),
    )
    call_coordinator_model: str | None = Field(
        default=None,
        description=(
            "The model the call's coordinator and its sub-agents run on, by "
            "public name. Empty uses the agent's own runtime."
        ),
    )
    call_max_minutes: int = Field(
        default=60,
        ge=1,
        le=240,
        description="The longest a call may last before it is ended for the caller.",
    )
    calls_per_user: int = Field(
        default=2,
        ge=1,
        le=20,
        description="Calls one person may have open at once, across replicas.",
    )
    call_quiet_window_ms: int = Field(
        default=700,
        ge=200,
        le=5000,
        description=(
            "How long both sides must be quiet before an update the agents "
            "asked to be said at the next pause is spoken."
        ),
    )

    @field_validator(
        "gemini_api_key",
        "gemini_vertex_project",
        "gemini_live_voice",
        "openai_api_key",
        "deepgram_api_key",
        "cascade_eager_eot_threshold",
        "cascade_talker_model",
        "cascade_talker_reasoning_effort",
        "call_coordinator_model",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # The documented `.env` writes these as `KEY=`. Read verbatim, a blank
        # key would offer an engine that cannot connect, and a blank threshold
        # would fail validation instead of meaning "wait for the confirmed end".
        if isinstance(value, str) and not value.strip():
            return None
        return value


calls_settings = CallsSettings()
