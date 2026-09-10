"""Accounting failures retain useful UI classifications without provider data."""

import httpx
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
from app.modules.usage.infrastructure.provider_retries import is_harness_owned_drop
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


@pytest.mark.parametrize(
    "dropped",
    [
        httpx.ReadError("connection died"),
        httpx.ConnectError("never opened"),
        TimeoutError("no bytes"),
    ],
)
def test_a_dropped_connection_is_left_for_the_harness(dropped: Exception) -> None:
    """The metering layer must not retry what the harness reports.

    A drop while the stream is opening is the same failure as one mid-answer,
    and the harness owns both: it resumes from recorded messages and emits a
    ``stream_reset`` so the client discards the half-answer. Retrying here
    hides that, and the exhausted attempts arrive as
    ``ProviderAttemptsExhaustedError`` -- which is how a dropped connection came
    to be reported as "temporarily unavailable, try again later" instead of the
    sentence that says nothing was lost.
    """
    assert is_harness_owned_drop(dropped)
    assert is_retryable_stream_error(dropped)


def test_a_drop_wrapped_by_the_provider_client_is_still_the_harness_s() -> None:
    # openai and anthropic both wrap the transport error rather than raise it,
    # so a check that only looks at the outermost exception sees nothing.
    wrapped = RuntimeError("provider client")
    wrapped.__cause__ = httpx.ReadError("connection died")
    assert is_harness_owned_drop(wrapped)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_provider_that_answered_is_still_retried_here(status: int) -> None:
    """`Retry-After` handling is the point of retrying in this layer.

    The provider replied, so there is no partial stream to reset and nothing
    for the harness to resume from -- retrying underneath it costs nothing and
    is where the header is honoured.
    """
    assert not is_harness_owned_drop(ModelHTTPError(status, "model"))
