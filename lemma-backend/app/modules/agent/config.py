"""Agent module configuration.

Field names are unchanged from the former monolithic ``Settings`` so the
environment variables resolve identically (``WIDGET_URL_SECRET``,
``DEEPGRAM_API_KEY``, …).

NOTE: the server-provided system LLM *model profile* (``LEMMA_*``), web search
(``WEB_SEARCH_*``) and embeddings (``EMBEDDING_*``) stay in core config — they
are cross-cutting platform capabilities consumed by ``app/core/*``, scripts and
the test harness, not purely agent-internal.
"""

from pathlib import Path
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.settings_env import dotenv_path


def _default_local_runtime_config_path() -> str:
    return str(
        Path(__file__).resolve().parents[4] / ".local" / "lemma" / "agent-runtime.json"
    )


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    agent_run_stop_poll_interval_seconds: float = Field(
        default=1.0,
        description="Minimum interval between database polls of an agent run's stop flag.",
    )
    agent_context_brief_cache_ttl_seconds: int = Field(
        default=60,
        description="TTL for cached rendered agent runtime-context briefs; zero disables caching.",
    )
    function_run_poll_interval_seconds: float = Field(
        default=5.0,
        description="Interval an agent tool waits between function-run status polls.",
    )
    conversation_title_model: str | None = Field(
        default=None,
        description="Optional model used to generate conversation titles.",
    )
    daemon_ws_ping_stale_after_seconds: float = Field(
        default=90.0,
        description="Close a user-daemon websocket after this many seconds without a ping.",
    )
    daemon_reconnect_grace_seconds: float = Field(
        default=120.0,
        description="Time allowed for a disconnected daemon to reattach an in-flight run.",
    )
    local_agent_runtime_config_path: str = Field(
        default_factory=_default_local_runtime_config_path,
        description="Local file containing the persisted system agent runtime default.",
    )

    # Conversation-widget embed URL signing.
    # Tokens are signed by the unified app/core/crypto signer (HKDF off the
    # required SECRET_ENCRYPTION_KEY) — no per-feature secret is configured here.
    widget_url_expiry_seconds: int = Field(
        default=1800,
        description="Lifetime (seconds) of a signed conversation-widget embed URL.",
    )

    # Speech (STT/TTS) toolset
    speech_provider: Literal["auto", "deepgram"] = Field(
        default="auto",
        description=(
            "Speech (STT/TTS) backend for the agent speech toolset. Currently "
            "only deepgram; auto selects the first available provider."
        ),
    )
    deepgram_api_key: Optional[str] = Field(
        default=None,
        description="Deepgram API key for the speech toolset (listen/say).",
    )


agent_settings = AgentSettings()
