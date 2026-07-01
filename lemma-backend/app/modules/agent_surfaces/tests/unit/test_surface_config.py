"""Golden test for agent_surfaces config: env-var names + defaults preserved."""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.config import SurfaceSettings

pytestmark = pytest.mark.unit

# (field, ENV var, default) transcribed from the former app/core/config.py.
EXPECTED = [
    ("microsoft_bot_app_id", "MICROSOFT_BOT_APP_ID", None),
    ("microsoft_bot_app_password", "MICROSOFT_BOT_APP_PASSWORD", None),
    ("microsoft_bot_tenant_id", "MICROSOFT_BOT_TENANT_ID", None),
    ("microsoft_bot_openid_config_url", "MICROSOFT_BOT_OPENID_CONFIG_URL", None),
    ("slack_signing_secret", "SLACK_SIGNING_SECRET", None),
    ("slack_app_token", "SLACK_APP_TOKEN", None),
    ("slack_bot_token", "SLACK_BOT_TOKEN", None),
    ("whatsapp_access_token", "WHATSAPP_ACCESS_TOKEN", None),
    ("whatsapp_phone_number_id", "WHATSAPP_PHONE_NUMBER_ID", None),
    ("whatsapp_waba_id", "WHATSAPP_WABA_ID", None),
    ("whatsapp_verify_token", "WHATSAPP_VERIFY_TOKEN", None),
    ("whatsapp_app_secret", "WHATSAPP_APP_SECRET", None),
    ("telegram_bot_token", "TELEGRAM_BOT_TOKEN", None),
    ("telegram_webhook_secret", "TELEGRAM_WEBHOOK_SECRET", None),
    ("resend_api_key", "RESEND_API_KEY", None),
    ("resend_inbound_domain", "RESEND_INBOUND_DOMAIN", "ops.lemma.work"),
    ("resend_from_name", "RESEND_FROM_NAME", "Lemma"),
    ("resend_inbound_signing_secret", "RESEND_INBOUND_SIGNING_SECRET", None),
    ("surface_raw_webhook_log_dir", "SURFACE_RAW_WEBHOOK_LOG_DIR", None),
    ("surface_raw_webhook_log_sources", "SURFACE_RAW_WEBHOOK_LOG_SOURCES", ""),
    ("surface_webhook_security_enabled", "SURFACE_WEBHOOK_SECURITY_ENABLED", True),
    ("surface_event_dedupe_ttl_seconds", "SURFACE_EVENT_DEDUPE_TTL_SECONDS", 900),
    ("surface_runtime_history_max_messages", "SURFACE_RUNTIME_HISTORY_MAX_MESSAGES", 40),
    ("surface_runtime_history_window_hours", "SURFACE_RUNTIME_HISTORY_WINDOW_HOURS", 24),
    ("enable_telegram_polling_mode", "ENABLE_TELEGRAM_POLLING_MODE", False),
    ("enable_slack_socket_mode", "ENABLE_SLACK_SOCKET_MODE", False),
]


def _clear(monkeypatch):
    for _, env, _default in EXPECTED:
        monkeypatch.delenv(env, raising=False)


def test_surface_settings_defaults():
    # Assert the declared field defaults directly (no instantiation) so a
    # developer's local .env — which may carry real surface secrets — can't
    # shadow the code defaults this golden test pins.
    for field, _env, default in EXPECTED:
        assert SurfaceSettings.model_fields[field].default == default, field


def test_surface_settings_field_set_is_exact():
    assert set(SurfaceSettings.model_fields) == {f for f, _e, _d in EXPECTED}


@pytest.mark.parametrize("field,env,default", EXPECTED)
def test_surface_settings_reads_legacy_env_var(monkeypatch, field, env, default):
    _clear(monkeypatch)
    if isinstance(default, bool):
        raw, expected = ("false", False) if default else ("true", True)
    elif isinstance(default, int):
        raw, expected = "123", 123
    else:
        raw, expected = "sentinel", "sentinel"
    monkeypatch.setenv(env, raw)
    assert getattr(SurfaceSettings(), field) == expected
