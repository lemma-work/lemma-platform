import pytest

from app.modules.connectors.domain.errors import (
    OperationExecutionAccessDeniedError,
    OperationExecutionInfrastructureError,
    OperationExecutionNotFoundError,
    OperationExecutionUnauthorizedError,
    OperationExecutionValidationError,
)
from app.modules.connectors.infrastructure.adapters.lemma_operation_gateway import (
    LemmaOperationGateway,
)


class _ProviderError(Exception):
    def __init__(
        self, *, status_code=None, details=None, message="provider canary-secret"
    ):
        super().__init__(message)
        self.status_code = status_code
        self.details = details


@pytest.mark.parametrize(
    ("status_code", "provider_code", "expected_type"),
    [
        (400, "bad_request", OperationExecutionValidationError),
        (401, "not_authed", OperationExecutionUnauthorizedError),
        (403, "missing_scope", OperationExecutionAccessDeniedError),
        (404, "not_found", OperationExecutionNotFoundError),
        (503, "temporarily_unavailable", OperationExecutionInfrastructureError),
    ],
)
def test_provider_errors_are_classified_without_leaking_exception_text(
    status_code,
    provider_code,
    expected_type,
):
    translated = LemmaOperationGateway()._translate_execution_error(
        "send_message",
        "slack",
        _ProviderError(
            status_code=status_code,
            details={"error": provider_code, "secret": "canary-secret"},
        ),
    )

    assert isinstance(translated, expected_type)
    assert "canary-secret" not in str(translated)
    assert translated.details == {
        "error_type": "_ProviderError",
        "upstream_status": status_code,
        "upstream_code": provider_code,
    }


def test_long_provider_error_code_is_not_reflected():
    translated = LemmaOperationGateway()._translate_execution_error(
        "send_message",
        "slack",
        _ProviderError(details={"error": "x" * 101}),
    )

    assert translated.details == {"error_type": "_ProviderError"}


def test_the_provider_s_own_words_reach_the_caller():
    """PS-CONN-032: report what the provider said, not a generic failure.

    The vendored clients build their message from the provider's status and
    response body, and attach no status code -- so the shared upstream-message
    heuristic, which decides from a status code, cannot recognise them. Without
    this, Gmail, Slack and Jira were the connectors that could not tell a
    caller "invalid_scope" apart from "message not found".
    """
    from lemma_connectors.core.errors import IntegrationExecutionError

    translated = LemmaOperationGateway()._translate_execution_error(
        "send_message",
        "slack",
        IntegrationExecutionError(
            "send_message failed: the provider returned HTTP 403. "
            '{"error": "missing_scope", "needed": "chat:write"}'
        ),
    )

    assert isinstance(translated, OperationExecutionAccessDeniedError)
    assert "missing_scope" in translated.details["upstream_message"]
    assert "chat:write" in translated.details["upstream_message"]


def test_our_own_failures_still_keep_their_text_to_themselves():
    """The narrowing that makes the above safe: only the vendored clients'
    own error type is a relay. Anything else reaching the translator is ours,
    and its text describes how we are built."""
    translated = LemmaOperationGateway()._translate_execution_error(
        "send_message",
        "slack",
        RuntimeError("internal canary-secret at /var/run/lemma"),
    )

    assert "upstream_message" not in translated.details
    assert "canary-secret" not in str(translated)


def test_a_provider_message_is_capped_rather_than_relayed_whole():
    """A provider may answer with an entire HTML page; that does not belong in
    a JSON error body."""
    from lemma_connectors.core.errors import IntegrationExecutionError

    translated = LemmaOperationGateway()._translate_execution_error(
        "send_message",
        "slack",
        IntegrationExecutionError("x" * 5000),
    )

    assert len(translated.details["upstream_message"]) <= 2001
