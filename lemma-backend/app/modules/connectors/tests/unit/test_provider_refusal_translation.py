"""A provider's deliberate 4xx is its answer, never Lemma's own 500.

The status map stopped at 400/401/403/404/422/429, and every other 4xx fell to
the catch-all as `OperationExecutionError`: our 500, "The connector operation
could not be completed." GitHub refusing a merge its branch rules block (405
"Repository rule violations found") or one whose head moved (409 "Head branch
was modified") read as a Lemma fault, marked its span as an error, and kept
GitHub's reason in the details only -- which the agent's connector tool never
shows the model, since it returns `str(exc)` and nothing else.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.modules.connectors.domain.errors import (
    OperationExecutionConflictError,
    OperationExecutionInfrastructureError,
    OperationExecutionRejectedError,
    OperationExecutionTimeoutError,
)
from app.modules.connectors.infrastructure.adapters.openapi_http_executor import (
    OpenApiHttpExecutor,
)
from app.modules.connectors.services.execution.plumbing import (
    execution_failures_translated,
)

pytestmark = pytest.mark.unit

# What `ConnectorOperationUseCases` records against the breaker.
BREAKER_COUNTS = (OperationExecutionInfrastructureError, OperationExecutionTimeoutError)

_PULLS_MERGE = {
    "mode": "openapi",
    "method": "PUT",
    "path": "/repos/{owner}/{repo}/pulls/{pull_number}/merge",
    "server_url": "https://api.github.com",
    "path_params": ["owner", "repo", "pull_number"],
    "query_params": [],
    "header_params": [],
    "request_body": {
        "content_type": "application/json",
        "field": "body",
        "binary_fields": [],
        "form_fields": [],
    },
    "response": {"binary": False},
}


async def _merge_answered(status: int, github_message: str) -> Exception:
    """Run GitHub's merge through the real executor and translator.

    The real executor rather than a hand-built exception, so the error under
    test is the one production raises: its status on the exception, the body
    folded into the exception's text, no `response` attached.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={
                "message": github_message,
                "documentation_url": "https://docs.github.com/rest/pulls/pulls",
                "status": str(status),
            },
        )

    executor = OpenApiHttpExecutor(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(Exception) as caught:
        with execution_failures_translated():
            await executor.execute(
                connector_id="github",
                operation_name="pulls_merge",
                execution=_PULLS_MERGE,
                payload={
                    "owner": "acme",
                    "repo": "crm",
                    "pull_number": 7,
                    "body": {"sha": "abc123"},
                },
                third_party_credentials={"access_token": "tok"},
            )
    return caught.value


async def test_a_merge_branch_rules_block_is_the_providers_refusal():
    error = await _merge_answered(
        405,
        'Repository rule violations found\n\nRequired status check "CI passed" '
        "is failing.\n\n",
    )

    assert isinstance(error, OperationExecutionRejectedError)
    assert error.status_code == 400
    assert error.code == "OPERATION_EXECUTION_REJECTED"
    assert not isinstance(error, BREAKER_COUNTS)
    assert error.details["upstream_status"] == 405


async def test_the_refusal_says_why_to_a_caller_reading_only_the_message():
    """The agent's connector tool hands the model `str(exc)`. Told only
    "refused", it can neither fix the cause nor report it."""
    error = await _merge_answered(
        405, 'Repository rule violations found. Required status check "CI passed"'
    )

    assert str(error).startswith("Connector provider refused the operation.")
    assert "Required status check" in str(error)


async def test_a_merge_whose_head_moved_is_a_conflict():
    """Re-read and decide again -- not "fix your arguments", not "try later"."""
    error = await _merge_answered(
        409, "Head branch was modified. Review and try the merge again."
    )

    assert isinstance(error, OperationExecutionConflictError)
    assert error.status_code == 409
    assert error.code == "OPERATION_EXECUTION_CONFLICT"
    assert not isinstance(error, BREAKER_COUNTS)
    assert "Head branch was modified" in str(error)


class _StatusCarrying(Exception):
    """How the http/sql/mcp executors report a provider status."""

    def __init__(self, status: int, text: str = "provider refused"):
        super().__init__(text)
        self.status_code = status


def _translate(raised: Exception) -> Exception:
    with pytest.raises(Exception) as caught:
        with execution_failures_translated():
            raise raised
    return caught.value


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://provider.example/thing")
    response = httpx.Response(status, request=request, text="provider refused")
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.mark.parametrize(
    "raise_as",
    [_StatusCarrying, _http_status_error],
    ids=["executor-error", "httpx-status-error"],
)
def test_no_provider_4xx_is_ever_our_500_or_an_outage(raise_as):
    """Every 4xx, not a list of the ones met so far: a list is what let 405 and
    409 through. Both branches of the translator, since an `HTTPStatusError`
    takes the transport branch and an executor's own error the other."""
    wrong = {}
    for status in range(400, 500):
        error = _translate(raise_as(status))
        if not 400 <= error.status_code < 500 or isinstance(error, BREAKER_COUNTS):
            wrong[status] = (type(error).__name__, error.status_code)

    assert wrong == {}


def test_a_refusal_with_nothing_said_keeps_the_plain_sentence():
    error = _translate(_StatusCarrying(405, ""))

    assert str(error) == "Connector provider refused the operation."


def test_the_quoted_reason_is_bounded_in_the_message():
    """A provider's refusal is sometimes a whole firewall page. The details keep
    more of it; the message is a sentence someone reads."""
    error = _translate(_StatusCarrying(403, "<html>" + "x" * 50_000))

    assert len(str(error)) < 600
    assert str(error).endswith("…")
    assert len(error.details["upstream_message"]) > len(str(error))


def test_a_credential_echoed_in_a_refusal_stays_out_of_the_message():
    token = "ghp_" + "x" * 36
    error = _translate(
        _StatusCarrying(405, json.dumps({"message": f"bad header token {token}"}))
    )

    assert token not in str(error)
    assert "bad header token" in str(error)


def test_an_outage_does_not_quote_the_provider():
    """A gateway's error page explains nothing, and the response to an outage
    is the same whatever it says."""
    error = _translate(_StatusCarrying(503, "upstream connect error or disconnect"))

    assert isinstance(error, OperationExecutionInfrastructureError)
    assert str(error) == "Connector provider is temporarily unavailable."
