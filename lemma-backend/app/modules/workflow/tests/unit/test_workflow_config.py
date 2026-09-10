"""Golden test for workflow config: env-var names + defaults preserved.

The field set is exact and every default is asserted, for the same reason the
datastore and agent versions of this file are: these came out of
`app/core/config.py`, and a value drifting in the move is the failure mode that
looks like nothing. Transcribed from `Settings` before the move and checked
against it -- all 3 came across unchanged.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.modules.workflow.config import WorkflowSettings

pytestmark = pytest.mark.unit

# (field, ENV var, default)
EXPECTED = [
    ("workflow_wait_retention_days", "WORKFLOW_WAIT_RETENTION_DAYS", 30),
    ("workflow_wait_retention_batch_size", "WORKFLOW_WAIT_RETENTION_BATCH_SIZE", 1000),
    (
        "workflow_wait_retention_budget_seconds",
        "WORKFLOW_WAIT_RETENTION_BUDGET_SECONDS",
        45.0,
    ),
]


def test_workflow_settings_field_set_is_exact():
    assert set(WorkflowSettings.model_fields) == {
        field for field, _env, _default in EXPECTED
    }


def test_workflow_settings_defaults():
    # Declared defaults only -- immune to a developer's local .env / os.environ.
    for field, _env, default in EXPECTED:
        actual = WorkflowSettings.model_fields[field].default
        if isinstance(default, SecretStr):
            assert isinstance(actual, SecretStr) or actual is None, field
            continue
        assert actual == default, field
