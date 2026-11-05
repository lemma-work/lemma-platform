"""A provider refusing the connected account's key is not the caller's session ending.

Every client of this API reads a 401 as "your Lemma session is gone". The
browser's session interceptor refreshes and resends the request, the SDKs and
the CLI refresh and run it again, and a pod app signs its user out. A
connector whose third-party API key is wrong answered exactly that, so the
operation ran several more times against the provider and then a person who
was signed in landed on the sign-in page.

Both translation paths are covered: the one every http/sql/mcp executor goes
through, and Composio's, which classifies its own failures.
"""

from __future__ import annotations

import os

import httpx
import pytest

os.environ.setdefault("COMPOSIO_CACHE_DIR", "/tmp/composio")

from app.modules.connectors.domain.errors import (
    ConnectorDomainError,
    OperationExecutionUnauthorizedError,
)
from app.modules.connectors.infrastructure.adapters.composio_operation_gateway import (
    ComposioOperationGateway,
)
from app.modules.connectors.services.execution.plumbing import (
    execution_failures_translated,
)

pytestmark = pytest.mark.unit

_EVERY_PROVIDER_ERROR_STATUS = range(400, 600)


def _provider_refusal(status: int, body: str = "") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.example.com/v1/things")
    response = httpx.Response(status, request=request, text=body)
    return httpx.HTTPStatusError("refused", request=request, response=response)


def _translated(status: int) -> ConnectorDomainError:
    with pytest.raises(ConnectorDomainError) as caught:
        with execution_failures_translated():
            raise _provider_refusal(status)
    return caught.value


def test_a_rejected_api_key_reaches_the_caller_as_a_failed_dependency():
    with pytest.raises(OperationExecutionUnauthorizedError) as caught:
        with execution_failures_translated():
            raise _provider_refusal(401, '{"error":"invalid_api_key"}')

    error = caught.value
    assert error.status_code == 424
    assert error.code == "OPERATION_EXECUTION_UNAUTHORIZED"
    assert error.details["upstream_status"] == 401
    assert "invalid_api_key" in error.details["upstream_message"]


def test_no_provider_status_reaches_the_caller_as_a_session_401():
    answered_401 = [
        status
        for status in _EVERY_PROVIDER_ERROR_STATUS
        if _translated(status).status_code == 401
    ]

    assert answered_401 == []


def test_a_composio_401_reaches_the_caller_as_a_failed_dependency():
    error = ComposioOperationGateway._classify_failure(
        "GOOGLEDRIVE_LIST_FILES", 401, "unauthorized", {"provider": "composio"}
    )

    assert isinstance(error, OperationExecutionUnauthorizedError)
    assert error.status_code == 424


@pytest.mark.parametrize("token", ["unauthorized", "not_authed", "invalid_auth"])
def test_a_composio_auth_token_without_a_status_is_not_a_session_401(token):
    error = ComposioOperationGateway._classify_failure(
        "SLACK_SEND_MESSAGE", None, token, {"provider": "composio"}
    )

    assert isinstance(error, OperationExecutionUnauthorizedError)
    assert error.status_code == 424
