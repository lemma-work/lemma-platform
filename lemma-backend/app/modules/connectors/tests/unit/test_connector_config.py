"""Golden test for connector config: env-var names + defaults preserved."""

from __future__ import annotations

import pytest

from app.modules.connectors.config import ConnectorSettings

pytestmark = pytest.mark.unit

EXPECTED = [
    ("composio_api_key", "COMPOSIO_API_KEY", None, "sentinel"),
    ("composio_webhook_secret", "COMPOSIO_WEBHOOK_SECRET", None, "sentinel"),
    (
        "composio_sdk_telemetry_enabled",
        "COMPOSIO_SDK_TELEMETRY_ENABLED",
        False,
        True,
    ),
    (
        "connector_operation_timeout_seconds",
        "CONNECTOR_OPERATION_TIMEOUT_SECONDS",
        45.0,
        5.0,
    ),
    (
        "connector_discovery_timeout_seconds",
        "CONNECTOR_DISCOVERY_TIMEOUT_SECONDS",
        25.0,
        5.0,
    ),
    (
        "connector_spec_max_bytes",
        "CONNECTOR_SPEC_MAX_BYTES",
        8 * 1024 * 1024,
        1024,
    ),
    (
        "connector_credential_refresh_skew_seconds",
        "CONNECTOR_CREDENTIAL_REFRESH_SKEW_SECONDS",
        120.0,
        30.0,
    ),
    (
        "connector_sql_engine_cache_size",
        "CONNECTOR_SQL_ENGINE_CACHE_SIZE",
        32,
        4,
    ),
    (
        "connector_composio_managed_files_enabled",
        "CONNECTOR_COMPOSIO_MANAGED_FILES_ENABLED",
        False,
        True,
    ),
    (
        "connector_inline_result_max_bytes",
        "CONNECTOR_INLINE_RESULT_MAX_BYTES",
        1024 * 1024,
        2048,
    ),
    (
        "connector_response_max_bytes",
        "CONNECTOR_RESPONSE_MAX_BYTES",
        64 * 1024 * 1024,
        4096,
    ),
    ("connector_encryption_key", "CONNECTOR_ENCRYPTION_KEY", None, "sentinel"),
]


def _clear(monkeypatch):
    for _, env, _default, _override in EXPECTED:
        monkeypatch.delenv(env, raising=False)


def test_connector_settings_defaults():
    # Declared defaults only — immune to a developer's local .env / os.environ.
    for field, _env, default, _override in EXPECTED:
        assert ConnectorSettings.model_fields[field].default == default, field


def test_connector_settings_field_set_is_exact():
    assert set(ConnectorSettings.model_fields) == {f for f, _e, _d, _o in EXPECTED}


@pytest.mark.parametrize("field,env,_default,override", EXPECTED)
def test_connector_settings_reads_legacy_env_var(
    monkeypatch, field, env, _default, override
):
    _clear(monkeypatch)
    monkeypatch.setenv(
        env, str(override).lower() if isinstance(override, bool) else str(override)
    )
    assert getattr(ConnectorSettings(), field) == override
