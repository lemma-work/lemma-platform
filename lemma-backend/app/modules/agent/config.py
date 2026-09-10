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

from pydantic import Field, SecretStr
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
        default=6000,
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
    agent_model_context_windows: str = Field(
        default="",
        description=(
            "Per-model context windows an operator declares, as comma-separated "
            "`name=tokens` pairs (e.g. 'claude-sonnet-4=200000,kimi-k3=131072'). "
            "Used where a provider's /models payload does not advertise one, "
            "which is most of them. Unlisted models use the default below."
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
    agent_model_stream_first_chunk_timeout_seconds: float = Field(
        default=60.0,
        description=(
            "How long a provider may take to send the first chunk of a response "
            "body before the request is abandoned and retried. Unlike the "
            "per-chunk read timeout above, a provider cannot reset this one, so "
            "it is what catches a request that was accepted and never started. "
            "0 disables it."
        ),
    )
    agent_model_stream_total_timeout_seconds: float = Field(
        default=300.0,
        description=(
            "Ceiling on one whole model exchange, first byte to last. The only "
            "bound that catches a provider trickling a token a second: the "
            "per-chunk timeout never fires while chunks keep arriving. Generous "
            "on purpose, because a long answer streaming steadily has to be "
            "allowed to finish. 0 disables it."
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

    # Moved out of `app/core/config.py`. Every production reader is in
    # `mod:agent`; two are also read by the e2e worker-subprocess environment,
    # repointed with them.
    #
    # `lemma_openai_api_key` and `lemma_openai_base_url` are deliberately NOT
    # here: core's embeddings, datastore's reranker and
    # `scripts/import_connector_catalog.py` read them too, so they are shared
    # credentials rather than agent's. Nor are the six `llm_otel_*`, which only
    # `core/observability/telemetry.py` reads despite the name.
    lemma_anthropic_api_key: Optional[SecretStr] = Field(
        default=None,
        description="API key for the server-provided Anthropic-compatible Lemma model profile.",
    )
    lemma_anthropic_base_url: str = Field(
        default="https://api.anthropic.com",
        description="Base URL for the server-provided Anthropic-compatible Lemma model profile.",
    )
    lemma_anthropic_default_model: str = Field(
        default="claude-sonnet-4-5",
        description="Default public model name for the server-provided Anthropic-compatible Lemma profile.",
    )
    lemma_anthropic_model_names: str = Field(
        default="claude-sonnet-4-5,claude-haiku-4-5",
        description="Comma-separated public model names for the server-provided Anthropic-compatible Lemma profile.",
    )
    lemma_default_model_type: Literal["openai_compat", "anthropic_compat"] = Field(
        default="openai_compat",
        description="Server-provided Lemma system model profile provider type.",
    )
    lemma_llm_caching_enabled: bool = Field(
        default=False,
        description=(
            "Enable LLM prompt caching. Activates PromptCachingCapability, which "
            "applies conversation-id session affinity on OPENAI_COMPATIBLE profiles "
            "(e.g. Fireworks via lemma-cloud) and an explicit instruction cache "
            "breakpoint on ANTHROPIC_COMPATIBLE profiles."
        ),
    )
    lemma_openai_default_model: str = Field(
        default="",
        description=(
            "Default model name for the OpenAI-compatible system model profile. "
            "No built-in default: when LEMMA_OPENAI_API_KEY is set the model(s) "
            "must be provided via LEMMA_OPENAI_MODEL_NAMES / "
            "LEMMA_OPENAI_DEFAULT_MODEL, otherwise the profile build fails loudly."
        ),
    )
    lemma_openai_model_names: str = Field(
        default="",
        description=(
            "Comma-separated model names for the OpenAI-compatible system model "
            "profile. Required (via env) when LEMMA_OPENAI_API_KEY is set; there "
            "is no built-in model default."
        ),
    )
    lemma_openai_vision_model_names: str = Field(
        default="",
        description=(
            "Comma-separated subset of LEMMA_OPENAI_MODEL_NAMES whose models accept "
            "image input. Gates the image-returning tools (view_image): a text-only "
            "model breaks when image content enters its history, so those tools are "
            "withheld unless a model is listed here. The standard OpenAI /models "
            "endpoint does not report modalities, so vision must be declared "
            "explicitly here; leave empty if no configured model supports vision. "
            "(Provider-discovered profiles can additionally auto-detect image input "
            "when the provider advertises it.)"
        ),
    )


agent_settings = AgentSettings()
