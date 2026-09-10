"""Quota explanations survive nested task groups without hiding configuration failures."""

from app.modules.agent.services.run_finalizer import (
    is_usage_limit_error,
    run_failure_code,
    run_failure_message,
    run_failure_reason,
)
from app.modules.agent.services.realtime import error_payload
from app.modules.usage.domain.errors import UsageLimitExceededError
from uuid import uuid4


def test_pricing_failure_does_not_claim_allowance_exhaustion() -> None:
    error = ExceptionGroup(
        "model call",
        [UsageLimitExceededError("A known price is required", reason="configuration")],
    )
    assert not is_usage_limit_error(error)
    assert run_failure_reason(error) == "configuration"
    assert run_failure_message(error) == "A known price is required"
    payload = error_payload(
        uuid4(),
        run_failure_message(error),
        code=run_failure_code(error),
        reason=run_failure_reason(error),
    )
    assert payload["data"] == "A known price is required"
    assert payload["error_reason"] == "configuration"


def test_nested_exhaustion_retains_structured_reason() -> None:
    error = ExceptionGroup(
        "run", [ExceptionGroup("request", [UsageLimitExceededError()])]
    )
    assert is_usage_limit_error(error)
    assert run_failure_code(error) == "USAGE_LIMIT_EXCEEDED"
    assert run_failure_reason(error) == "exhausted"
    assert "usage allowance" in run_failure_message(error)
