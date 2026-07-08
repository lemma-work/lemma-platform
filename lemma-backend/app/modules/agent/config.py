"""Agent module configuration (conversation-widget signing + speech toolset).

Field names are unchanged from the former monolithic ``Settings`` so the
environment variables resolve identically (``WIDGET_URL_SECRET``,
``DEEPGRAM_API_KEY``, …).

NOTE: the server-provided system LLM *model profile* (``LEMMA_*``), web search
(``WEB_SEARCH_*``) and embeddings (``EMBEDDING_*``) stay in core config — they
are cross-cutting platform capabilities consumed by ``app/core/*``, scripts and
the test harness, not purely agent-internal.
"""

from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.settings_env import dotenv_path


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=dotenv_path(), env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
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
