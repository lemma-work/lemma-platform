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
    ("function_run_poll_interval_seconds", "FUNCTION_RUN_POLL_INTERVAL_SECONDS", 5.0),
    ("conversation_title_model", "CONVERSATION_TITLE_MODEL", None),
    ("daemon_ws_ping_stale_after_seconds", "DAEMON_WS_PING_STALE_AFTER_SECONDS", 90.0),
    ("daemon_reconnect_grace_seconds", "DAEMON_RECONNECT_GRACE_SECONDS", 120.0),
    ("widget_url_expiry_seconds", "WIDGET_URL_EXPIRY_SECONDS", 1800),
    ("speech_provider", "SPEECH_PROVIDER", "auto"),
    ("deepgram_api_key", "DEEPGRAM_API_KEY", None),
]
FACTORY_FIELDS = {"local_agent_runtime_config_path"}


def _clear(monkeypatch):
    for _, env, _default in EXPECTED:
        monkeypatch.delenv(env, raising=False)


def test_agent_settings_defaults():
    # Declared defaults only — immune to a developer's local .env / os.environ.
    for field, _env, default in EXPECTED:
        assert AgentSettings.model_fields[field].default == default, field


def test_agent_settings_field_set_is_exact():
    assert set(AgentSettings.model_fields) == {
        *(f for f, _e, _d in EXPECTED),
        *FACTORY_FIELDS,
    }


def test_agent_runtime_config_path_default_and_env(monkeypatch):
    field = AgentSettings.model_fields["local_agent_runtime_config_path"]
    assert field.default_factory is not None
    assert field.default_factory().endswith("/.local/lemma/agent-runtime.json")
    monkeypatch.setenv("LOCAL_AGENT_RUNTIME_CONFIG_PATH", "/tmp/runtime.json")
    assert AgentSettings().local_agent_runtime_config_path == "/tmp/runtime.json"


@pytest.mark.parametrize("field,env,default", EXPECTED)
def test_agent_settings_reads_legacy_env_var(monkeypatch, field, env, default):
    _clear(monkeypatch)
    raw, expected = (
        ("123", float(123) if isinstance(default, float) else 123)
        if isinstance(default, (int, float))
        else (
            ("deepgram", "deepgram")
            if field == "speech_provider"
            else ("sentinel", "sentinel")
        )
    )
    monkeypatch.setenv(env, raw)
    assert getattr(AgentSettings(), field) == expected
