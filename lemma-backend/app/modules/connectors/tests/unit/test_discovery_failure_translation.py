"""What a caller is told when discovery fails against a tenant's own server.

Installing an MCP server that wants a token answered HTTP 500 with a Python
traceback in the response body. Two separate causes, both here:

* `KindDispatcher.discover` bounded the call in time and translated nothing
  else, so an `httpx.HTTPStatusError` escaped raw. Execute had the translation;
  discovery, which reaches the same tenant server over the same network, did
  not.
* The translator then read that error as an outage. `HTTPStatusError` is an
  `HTTPError`, so it matched the transport branch before anything looked for a
  status -- and a 401 came back as "temporarily unavailable", inviting the retry
  that function's own docstring says not to invite.
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.connectors.domain.errors import (
    OperationExecutionAccessDeniedError,
    OperationExecutionInfrastructureError,
    OperationExecutionNotFoundError,
    OperationExecutionUnauthorizedError,
)
from app.modules.connectors.services.execution.plumbing import (
    execution_failures_translated,
)

pytestmark = pytest.mark.unit


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://tenant.example/mcp")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, OperationExecutionUnauthorizedError),
        (403, OperationExecutionAccessDeniedError),
        (404, OperationExecutionNotFoundError),
    ],
)
def test_a_status_the_provider_chose_is_reported_as_that(status, expected):
    with pytest.raises(expected):
        with execution_failures_translated():
            raise _status_error(status)


def test_the_upstream_status_travels_in_the_details():
    with pytest.raises(OperationExecutionUnauthorizedError) as caught:
        with execution_failures_translated():
            raise _status_error(401)
    assert caught.value.details["upstream_status"] == 401


def test_a_status_with_no_mapping_is_still_an_outage():
    """502 really is "try again"; only the statuses that mean something
    specific are reclassified."""
    with pytest.raises(OperationExecutionInfrastructureError):
        with execution_failures_translated():
            raise _status_error(502)


def test_a_genuine_transport_failure_is_still_an_outage():
    with pytest.raises(OperationExecutionInfrastructureError):
        with execution_failures_translated():
            raise httpx.ConnectError("no route to host")


def test_the_provider_gets_to_explain_itself():
    """Hiding this left a caller with a status code and nothing else -- no way
    to tell `invalid_scope` from `repository not found`. Only *our* internals
    are hidden; the provider's own words are the diagnosis."""
    request = httpx.Request("POST", "https://tenant.example/mcp")
    response = httpx.Response(
        403, request=request, text='{"error":"insufficient_scope"}'
    )
    with pytest.raises(OperationExecutionAccessDeniedError) as caught:
        with execution_failures_translated():
            raise httpx.HTTPStatusError("boom", request=request, response=response)
    assert "insufficient_scope" in caught.value.details["upstream_message"]


def test_a_credential_the_provider_echoed_back_is_scrubbed():
    """The one place a secret can travel by accident: some providers quote the
    token they rejected."""
    secret = "sk-proj-" + "a1b2c3d4e5" * 4
    request = httpx.Request("POST", "https://tenant.example/mcp")
    response = httpx.Response(401, request=request, text=f"token {secret} rejected")
    with pytest.raises(OperationExecutionUnauthorizedError) as caught:
        with execution_failures_translated():
            raise httpx.HTTPStatusError("boom", request=request, response=response)
    rendered = str(caught.value.details)
    assert secret not in rendered
    assert "rejected" in rendered


def test_an_authorization_header_quoted_back_is_scrubbed():
    request = httpx.Request("POST", "https://tenant.example/mcp")
    response = httpx.Response(
        401, request=request, text="Authorization: Bearer ghp_" + "x" * 36
    )
    with pytest.raises(OperationExecutionUnauthorizedError) as caught:
        with execution_failures_translated():
            raise httpx.HTTPStatusError("boom", request=request, response=response)
    assert "ghp_" not in str(caught.value.details)


def test_an_enormous_error_page_is_bounded():
    """A provider may answer with an entire HTML page."""
    request = httpx.Request("POST", "https://tenant.example/mcp")
    response = httpx.Response(500, request=request, text="x" * 50_000)
    with pytest.raises(OperationExecutionInfrastructureError) as caught:
        with execution_failures_translated():
            raise httpx.HTTPStatusError("boom", request=request, response=response)
    assert len(caught.value.details["upstream_message"]) < 2100
