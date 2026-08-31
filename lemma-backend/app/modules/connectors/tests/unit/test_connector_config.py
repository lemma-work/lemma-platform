"""Golden test for connector config: env-var names + defaults preserved."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

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
    ("connector_breaker_enabled", "CONNECTOR_BREAKER_ENABLED", True, False),
    (
        "connector_breaker_failure_threshold",
        "CONNECTOR_BREAKER_FAILURE_THRESHOLD",
        5,
        2,
    ),
    (
        "connector_breaker_cooldown_seconds",
        "CONNECTOR_BREAKER_COOLDOWN_SECONDS",
        60,
        10,
    ),
    (
        "connector_breaker_failure_window_seconds",
        "CONNECTOR_BREAKER_FAILURE_WINDOW_SECONDS",
        120,
        30,
    ),
    (
        # Equal to the dispatcher's Composio per-kind ceiling on purpose, so the
        # gateway backstop never pre-empts the routed path's tighter timeouts.
        "connector_composio_deadline_seconds",
        "CONNECTOR_COMPOSIO_DEADLINE_SECONDS",
        90.0,
        5.0,
    ),
    ("connector_github_app_slug", "CONNECTOR_GITHUB_APP_SLUG", None, "lemma-tester"),
    (
        "connector_github_app_private_key",
        "CONNECTOR_GITHUB_APP_PRIVATE_KEY",
        None,
        "sentinel",
    ),
    (
        "connector_github_app_private_key_path",
        "CONNECTOR_GITHUB_APP_PRIVATE_KEY_PATH",
        None,
        "/tmp/app.pem",
    ),
    (
        "connector_github_app_webhook_secret",
        "CONNECTOR_GITHUB_APP_WEBHOOK_SECRET",
        None,
        "sentinel",
    ),
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
    value = getattr(ConnectorSettings(), field)
    # The App private key and webhook secret are SecretStr, so that neither can
    # be printed by an unlucky traceback or repr.
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    assert value == override
