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
    def __init__(self, *, status_code=None, details=None, message="provider canary-secret"):
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
