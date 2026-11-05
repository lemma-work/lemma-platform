"""A transport failure is classified the same whichever HTTP stack raised it.

The provider SDKs run on ``httpx2``; tools, MCP servers and other callers still
raise ``httpx``. Neither hierarchy subclasses the other -- an
``httpx2.ReadTimeout`` is not an ``httpx.ReadTimeout`` -- so a classifier that
names one stack stops recognising the other's failures without any error. When
the model clients moved to ``httpx2``, every mid-stream drop would have stopped
being retried and been reported as an unclassified failure.
"""

from __future__ import annotations

import httpx
import httpx2
import pytest

from app.modules.agent.infrastructure.providers.transport_errors import (
    ModelResponseBudgetExceeded,
    connection_failure_message,
    is_retryable_stream_error,
    local_model_server_name,
    response_budget_expiry,
)
from app.modules.agent.services.run_finalizer import run_failure_message
from app.modules.usage.infrastructure.provider_retries import (
    confirmed_rejection,
    is_harness_owned_drop,
    retry_delay,
)

pytestmark = pytest.mark.unit

_URL = "https://provider.example.test/v1/chat/completions"
_LOOPBACK = "http://127.0.0.1:11434/v1/chat/completions"


def _pair(name: str, url: str = _URL) -> tuple[Exception, Exception]:
    """The same failure raised by each stack, request attached as SDKs do."""
    legacy: type[httpx.TransportError] = getattr(httpx, name)
    current: type[httpx2.TransportError] = getattr(httpx2, name)
    return (
        legacy("failed", request=httpx.Request("POST", url)),
        current("failed", request=httpx2.Request("POST", url)),
    )


class _SDKConnectionError(Exception):
    """Stands in for the SDKs' `APIConnectionError`, which wraps the cause."""


def _wrapped(cause: Exception) -> Exception:
    wrapper = _SDKConnectionError("Connection error.")
    wrapper.__cause__ = cause
    return wrapper


_FAILURES = ["ReadTimeout", "ConnectError", "RemoteProtocolError", "ReadError"]


@pytest.mark.parametrize("name", _FAILURES)
def test_both_stacks_are_retried_as_a_drop(name: str) -> None:
    legacy, current = _pair(name)

    assert is_retryable_stream_error(current) is True
    assert is_retryable_stream_error(current) == is_retryable_stream_error(legacy)


@pytest.mark.parametrize("name", _FAILURES)
def test_both_stacks_are_retried_when_an_sdk_wraps_them(name: str) -> None:
    legacy, current = _pair(name)

    assert is_retryable_stream_error(_wrapped(current)) is True
    assert is_retryable_stream_error(_wrapped(legacy)) is True


@pytest.mark.parametrize("name", _FAILURES)
def test_both_stacks_read_the_same_to_the_person(name: str) -> None:
    legacy, current = _pair(name)

    assert run_failure_message(current) == run_failure_message(legacy)
    assert run_failure_message(current) == connection_failure_message(current)


@pytest.mark.parametrize("name", _FAILURES)
def test_both_stacks_are_left_to_the_harness_by_metering(name: str) -> None:
    legacy, current = _pair(name)

    assert is_harness_owned_drop(current) is True
    assert is_harness_owned_drop(_wrapped(current)) is True
    assert is_harness_owned_drop(current) == is_harness_owned_drop(legacy)
    assert retry_delay(current, 1) == retry_delay(legacy, 1) == 2.0
    assert confirmed_rejection(current) is confirmed_rejection(legacy) is False


def test_a_refused_loopback_is_named_on_both_stacks() -> None:
    legacy, current = _pair("ConnectError", _LOOPBACK)

    assert local_model_server_name(_wrapped(current)) == "Ollama"
    assert local_model_server_name(_wrapped(legacy)) == "Ollama"


def test_a_connect_error_without_its_request_names_no_server() -> None:
    """Both stacks raise rather than return `None` for a missing request."""
    assert local_model_server_name(httpx2.ConnectError("refused")) is None
    assert local_model_server_name(httpx.ConnectError("refused")) is None


@pytest.mark.parametrize("status", [429, 503])
def test_a_status_error_is_retried_on_both_stacks(status: int) -> None:
    legacy = httpx.HTTPStatusError(
        "status",
        request=httpx.Request("POST", _URL),
        response=httpx.Response(status, headers={"retry-after": "3"}),
    )
    current = httpx2.HTTPStatusError(
        "status",
        request=httpx2.Request("POST", _URL),
        response=httpx2.Response(status, headers={"retry-after": "3"}),
    )

    assert retry_delay(current, 1) == retry_delay(legacy, 1) == 3.0
    assert confirmed_rejection(current) is confirmed_rejection(legacy)


def test_the_budget_expiry_is_an_httpx2_timeout_and_not_a_drop() -> None:
    """It is raised inside the httpx2 transport, but must never retry as a drop."""
    expiry = ModelResponseBudgetExceeded(
        "x",
        request=httpx2.Request("POST", _URL),
        elapsed_seconds=901.0,
        budget_seconds=900.0,
    )

    assert isinstance(expiry, httpx2.ReadTimeout)
    assert is_retryable_stream_error(expiry) is False
    assert is_retryable_stream_error(_wrapped(expiry)) is False
    assert response_budget_expiry(_wrapped(expiry)) is expiry
