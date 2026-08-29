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
    agent_memory_index_max_chars: int = Field(
        default=2000,
        description=(
            "Per-scope cap on the AGENTS.md text spliced into the runtime "
            "brief; the rest is truncated at a line boundary with a marker."
        ),
    )
    agent_memory_section_max_chars: int = Field(
        default=8000,
        description=(
            "Cap on the whole rendered memory section of the runtime brief, "
            "spent narrowest-scope-first."
        ),
    )
    agent_memory_brief_cache_ttl_seconds: int = Field(
        default=60,
        description=(
            "TTL for the cached memory section of the runtime brief; zero "
            "disables caching. Writes invalidate it, so this is only the "
            "backstop for an invalidation that never arrived."
        ),
    )
    function_run_poll_interval_seconds: float = Field(
        default=0.5,
        description="Interval an agent tool waits between function-run status polls.",
    )
    conversation_title_model: str | None = Field(
        default=None,
        description="Optional model used to generate conversation titles.",
    )
    vision_model: str | None = Field(
        default=None,
        description=(
            "Model used to look at images on behalf of agents whose own model "
            "cannot. Must be a model this deployment lists as accepting images. "
            "Unset means agents on text-only models cannot see images at all."
        ),
    )
    history_summarization_model: str | None = Field(
        default=None,
        description=(
            "Optional model used to compact conversation history. Defaults to "
            "the run's own model, which means every compaction is a ~70k-token "
            "request on the most expensive model in play; a small fast model is "
            "usually the better choice."
        ),
    )
    agent_default_context_window_tokens: int = Field(
        default=128_000,
        description=(
            "Context window assumed for a model whose catalog entry does not "
            "declare one. Compaction triggers at 80% of the window and the hard "
            "ceiling sits at 92%, so this is what an agent may actually work "
            "within. Per-model `metadata.context_window` overrides it."
        ),
    )

    # Model-request resilience. A provider that drops the SSE stream mid-response
    # used to fail the whole conversation run; the harness now resumes from the
    # messages already recorded instead.
    agent_model_stream_max_attempts: int = Field(
        default=3,
        ge=1,
        description=(
            "How many times a single agent run may re-enter the model graph after "
            "a transient transport failure. 1 disables retrying."
        ),
    )
    agent_model_http_connect_timeout_seconds: float = Field(
        default=10.0,
        description="Connect timeout for provider HTTP calls.",
    )
    agent_model_http_read_timeout_seconds: float = Field(
        default=180.0,
        description=(
            "Per-chunk read timeout for provider HTTP calls. This is the 'the "
            "provider has gone away' threshold, not a budget for the whole turn: "
            "httpx resets it on every chunk of a streamed response."
        ),
    )
    agent_model_http_max_connections: int = Field(
        default=100,
        description="Connection-pool ceiling per provider endpoint.",
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
    speech_stt_language: str = Field(
        default="multi",
        description=(
            "Default language for transcription when the caller names none. "
            "'multi' uses Nova-3 multilingual code-switching, which reads a "
            "voice note that mixes languages (Hinglish, Spanglish) word by "
            "word instead of forcing the whole file into one language. 'auto' "
            "uses Deepgram's whole-file language detection, which reaches more "
            "languages but commits to a single one. A BCP-47 code pins it."
        ),
    )
    speech_tts_voice: str = Field(
        default="aura-2-thalia-en",
        description=(
            "Default Aura-2 voice. Used when neither the caller nor the "
            "spoken language selects one."
        ),
    )
    speech_tts_bitrate: int = Field(
        default=48000,
        description=(
            "Bitrate (bits/sec) for compressed TTS output. Deepgram's own "
            "default for Opus is 12000, which is what a native voice note is "
            "encoded at on WhatsApp and Telegram, and it sounds like it."
        ),
    )

    # Web research
    web_fetch_impersonate_browser: bool = Field(
        default=True,
        description=(
            "Read pages for `web_fetch` through a client that replays a real "
            "Chrome TLS fingerprint. On, because sites fingerprint the handshake "
            "before reading User-Agent, and a refusal costs a browser render in "
            "the sandbox. Turn it off to fall back to the plain HTTP client "
            "without redeploying — the SSRF guard applies either way. Env: "
            "``WEB_FETCH_IMPERSONATE_BROWSER``."
        ),
    )


agent_settings = AgentSettings()
