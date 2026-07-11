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
