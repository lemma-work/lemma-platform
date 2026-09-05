"""Accounting failures retain useful UI classifications without provider data."""

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from app.modules.agent.infrastructure.harnesses.provider_error_log import (
    user_facing_error_message,
)
from app.modules.agent.infrastructure.transport_errors import is_retryable_stream_error
from app.modules.agent.services.run_finalizer import (
    is_usage_limit_error,
    run_failure_message,
)
from app.modules.usage.domain.accounting import AccountingConflictError
from app.modules.usage.domain.errors import (
    ProviderAttemptsExhaustedError,
    UsageCheckpointError,
    UsageLimitExceededError,
)


def test_accounting_conflict_exposes_safe_reason_and_is_not_retried() -> None:
    error = AccountingConflictError("internal-allocation-identifier")
    assert user_facing_error_message(error) == error.message
    assert "internal-allocation-identifier" not in error.message
    assert error.status_code == 409
    assert not is_retryable_stream_error(error)


@pytest.mark.parametrize("status", [429, 503])
def test_exhausted_provider_attempts_preserve_safe_http_guidance(status: int) -> None:
    provider = ModelHTTPError(status, "private-model", body="secret-provider-body")
    error = ProviderAttemptsExhaustedError()
    error.__cause__ = provider
    message = user_facing_error_message(error)
    assert f"HTTP {status}" in message
    assert "secret-provider-body" not in message
    assert "private-model" not in message
    assert not is_retryable_stream_error(error)


def test_nested_finalization_group_preserves_quota_classification() -> None:
    failure = ExceptionGroup(
        "execution and checkpoint failed",
        [
            ExceptionGroup("execution", [UsageLimitExceededError()]),
            UsageCheckpointError(),
        ],
    )
    assert is_usage_limit_error(failure)
    assert "available usage allowance" in run_failure_message(failure)


def test_checkpoint_failure_explains_retained_authority() -> None:
    error = UsageCheckpointError()
    assert "recorded for reconciliation" in run_failure_message(
        ExceptionGroup("finalization", [RuntimeError("private"), error])
    )
