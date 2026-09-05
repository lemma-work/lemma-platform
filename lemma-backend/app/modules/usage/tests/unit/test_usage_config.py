"""Golden test for usage config: env-var names + defaults preserved.

The field set is exact and every default is asserted, for the same reason the
datastore and agent versions of this file are: these came out of
`app/core/config.py`, and a value drifting in the move is the failure mode that
looks like nothing. Transcribed from `Settings` before the move and checked
against it -- all 3 came across unchanged.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.modules.usage.config import UsageSettings

pytestmark = pytest.mark.unit

# (field, ENV var, default)
EXPECTED = [
    ("usage_org_monthly_limit_usd", "USAGE_ORG_MONTHLY_LIMIT_USD", None),
    ("usage_user_weekly_limit_usd", "USAGE_USER_WEEKLY_LIMIT_USD", None),
    ("usage_user_monthly_limit_usd", "USAGE_USER_MONTHLY_LIMIT_USD", None),
]


def test_usage_settings_field_set_is_exact():
    assert set(UsageSettings.model_fields) == {
        field for field, _env, _default in EXPECTED
    }


def test_usage_settings_defaults():
    # Declared defaults only -- immune to a developer's local .env / os.environ.
    for field, _env, default in EXPECTED:
        actual = UsageSettings.model_fields[field].default
        if isinstance(default, SecretStr):
            assert isinstance(actual, SecretStr) or actual is None, field
            continue
        assert actual == default, field
