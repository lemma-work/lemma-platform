"""Golden test for agent config: env-var names + defaults preserved."""

from __future__ import annotations

import pytest

from app.modules.agent.config import AgentSettings

pytestmark = pytest.mark.unit

EXPECTED = [
    (
        "agent_run_stop_poll_interval_seconds",
        "AGENT_RUN_STOP_POLL_INTERVAL_SECONDS",
        1.0,
    ),
    (
        "agent_context_brief_cache_ttl_seconds",
        "AGENT_CONTEXT_BRIEF_CACHE_TTL_SECONDS",
        60,
    ),
    ("agent_memory_index_max_chars", "AGENT_MEMORY_INDEX_MAX_CHARS", 2000),
    ("agent_memory_section_max_chars", "AGENT_MEMORY_SECTION_MAX_CHARS", 6000),
    (
        "agent_memory_brief_cache_ttl_seconds",
        "AGENT_MEMORY_BRIEF_CACHE_TTL_SECONDS",
        60,
    ),
    ("function_run_poll_interval_seconds", "FUNCTION_RUN_POLL_INTERVAL_SECONDS", 0.5),
    ("conversation_title_model", "CONVERSATION_TITLE_MODEL", None),
    ("vision_model", "VISION_MODEL", None),
    ("history_summarization_model", "HISTORY_SUMMARIZATION_MODEL", None),
    ("agent_model_context_windows", "AGENT_MODEL_CONTEXT_WINDOWS", ""),
    (
        "agent_default_context_window_tokens",
        "AGENT_DEFAULT_CONTEXT_WINDOW_TOKENS",
        128_000,
    ),
    ("agent_model_stream_max_attempts", "AGENT_MODEL_STREAM_MAX_ATTEMPTS", 3),
    (
        "agent_model_http_connect_timeout_seconds",
        "AGENT_MODEL_HTTP_CONNECT_TIMEOUT_SECONDS",
        10.0,
    ),
    (
        "agent_model_http_read_timeout_seconds",
        "AGENT_MODEL_HTTP_READ_TIMEOUT_SECONDS",
        180.0,
    ),
    (
        "agent_model_stream_first_chunk_timeout_seconds",
        "AGENT_MODEL_STREAM_FIRST_CHUNK_TIMEOUT_SECONDS",
        60.0,
    ),
    (
        "agent_model_stream_total_timeout_seconds",
        "AGENT_MODEL_STREAM_TOTAL_TIMEOUT_SECONDS",
        300.0,
    ),
    ("agent_model_http_max_connections", "AGENT_MODEL_HTTP_MAX_CONNECTIONS", 100),
    ("widget_url_expiry_seconds", "WIDGET_URL_EXPIRY_SECONDS", 1800),
    ("speech_provider", "SPEECH_PROVIDER", "auto"),
    ("deepgram_api_key", "DEEPGRAM_API_KEY", None),
    ("speech_stt_language", "SPEECH_STT_LANGUAGE", "multi"),
    ("speech_tts_voice", "SPEECH_TTS_VOICE", "aura-2-thalia-en"),
    ("speech_tts_bitrate", "SPEECH_TTS_BITRATE", 48000),
    ("web_fetch_impersonate_browser", "WEB_FETCH_IMPERSONATE_BROWSER", True),
]
FACTORY_FIELDS = {"local_agent_runtime_config_path"}


def _clear(monkeypatch):
    for _, env, _default in EXPECTED:
        monkeypatch.delenv(env, raising=False)


# Moved out of `app/core/config.py`. Listed here for the same reason as
# everything above: the field set is exact, so a field arriving or leaving
# has to be a decision. Defaults transcribed from core before the move and
# checked against it -- all nine came across unchanged.
#
# `lemma_openai_api_key` and `lemma_openai_base_url` are NOT here: core
# embeddings, datastore's reranker and a catalog script read them too, so
# they are shared credentials rather than the agent module's.
MOVED_FROM_CORE = [
    ("lemma_anthropic_api_key", "LEMMA_ANTHROPIC_API_KEY", None),
    (
        "lemma_anthropic_base_url",
        "LEMMA_ANTHROPIC_BASE_URL",
        "https://api.anthropic.com",
    ),
    (
        "lemma_anthropic_default_model",
        "LEMMA_ANTHROPIC_DEFAULT_MODEL",
        "claude-sonnet-4-5",
    ),
    (
        "lemma_anthropic_model_names",
        "LEMMA_ANTHROPIC_MODEL_NAMES",
        "claude-sonnet-4-5,claude-haiku-4-5",
    ),
    ("lemma_default_model_type", "LEMMA_DEFAULT_MODEL_TYPE", "openai_compat"),
    ("lemma_llm_caching_enabled", "LEMMA_LLM_CACHING_ENABLED", False),
    ("lemma_openai_default_model", "LEMMA_OPENAI_DEFAULT_MODEL", ""),
    ("lemma_openai_model_names", "LEMMA_OPENAI_MODEL_NAMES", ""),
    ("lemma_openai_vision_model_names", "LEMMA_OPENAI_VISION_MODEL_NAMES", ""),
]


def test_agent_settings_defaults():
    # Declared defaults only — immune to a developer's local .env / os.environ.
    for field, _env, default in EXPECTED:
        assert AgentSettings.model_fields[field].default == default, field


def test_agent_settings_field_set_is_exact():
    assert set(AgentSettings.model_fields) == {
        *(f for f, _e, _d in EXPECTED),
        *(f for f, _e, _d in MOVED_FROM_CORE),
        *FACTORY_FIELDS,
    }


def test_agent_runtime_config_path_default_and_env(monkeypatch):
    field = AgentSettings.model_fields["local_agent_runtime_config_path"]
    assert field.default_factory is not None
    assert field.default_factory().endswith("/.local/lemma/agent-runtime.json")
    monkeypatch.setenv("LOCAL_AGENT_RUNTIME_CONFIG_PATH", "/tmp/runtime.json")
    assert AgentSettings().local_agent_runtime_config_path == "/tmp/runtime.json"


def _override_for(default, field) -> tuple[str, object]:
    """An env value that differs from the declared default.

    Differing is the point: if the override matched the default, the assertion
    would pass just as well when the variable was never consulted at all.
    """
    # Before the numeric case, because `isinstance(True, int)` is True in
    # Python — a flag would otherwise be handed "123" to parse.
    if isinstance(default, bool):
        return "false", False
    if isinstance(default, (int, float)):
        return "123", float(123) if isinstance(default, float) else 123
    if field == "speech_provider":
        return "deepgram", "deepgram"
    return "sentinel", "sentinel"


@pytest.mark.parametrize("field,env,default", EXPECTED)
def test_agent_settings_reads_legacy_env_var(monkeypatch, field, env, default):
    _clear(monkeypatch)
    raw, expected = _override_for(default, field)
    monkeypatch.setenv(env, raw)
    assert getattr(AgentSettings(), field) == expected
