from __future__ import annotations

import httpx
import pytest

from app.modules.workspace import agentbox_retry
from app.modules.workspace.agentbox_retry import (
    CONNECT_PHASE_TRANSPORT_ERRORS,
    is_retryable_http_error,
    retry_on_transient_agentbox_error,
)

pytestmark = pytest.mark.asyncio


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://agentbox.test/x")
    response = httpx.Response(status_code, request=request, text="{}")
    return httpx.HTTPStatusError("error", request=request, response=response)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _instant(_delay):
        return None

    monkeypatch.setattr(agentbox_retry.asyncio, "sleep", _instant)


async def test_returns_first_success_without_retry():
    calls = {"n": 0}

    async def _op():
        calls["n"] += 1
        return "ok"

    assert await retry_on_transient_agentbox_error(_op) == "ok"
    assert calls["n"] == 1


async def test_retries_retryable_http_then_succeeds():
    calls = {"n": 0}

    async def _op():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_status_error(502)
        return "ok"

    result = await retry_on_transient_agentbox_error(_op, max_attempts=3)
    assert result == "ok"
    assert calls["n"] == 2


async def test_retries_transport_error_then_succeeds():
    calls = {"n": 0}

    async def _op():
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("Connection refused")
        return "ok"

    assert await retry_on_transient_agentbox_error(_op, max_attempts=3) == "ok"
    assert calls["n"] == 2


async def test_non_retryable_http_raises_immediately():
    calls = {"n": 0}

    async def _op():
        calls["n"] += 1
        raise _http_status_error(400)

    with pytest.raises(httpx.HTTPStatusError):
        await retry_on_transient_agentbox_error(_op, max_attempts=5)
    assert calls["n"] == 1


async def test_exhaustion_reraises_last_error():
    calls = {"n": 0}

    async def _op():
        calls["n"] += 1
        raise _http_status_error(503)

    with pytest.raises(httpx.HTTPStatusError):
        await retry_on_transient_agentbox_error(_op, max_attempts=3)
    assert calls["n"] == 3


async def test_non_transport_exception_not_retried():
    calls = {"n": 0}

    async def _op():
        calls["n"] += 1
        raise ValueError("real error")

    with pytest.raises(ValueError):
        await retry_on_transient_agentbox_error(_op, max_attempts=5)
    assert calls["n"] == 1


async def test_is_retryable_http_error_matrix():
    assert is_retryable_http_error(_http_status_error(502))
    assert is_retryable_http_error(_http_status_error(500))
    assert not is_retryable_http_error(_http_status_error(400))
    assert not is_retryable_http_error(_http_status_error(404))


# --- non-idempotent (narrowed transport set) -------------------------------
# A synchronous function execute passes CONNECT_PHASE_TRANSPORT_ERRORS so a
# response-leg failure (which may have already run the function) is NOT re-sent.


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadError("read failed"),
        httpx.ReadTimeout("timed out"),
        httpx.RemoteProtocolError("server disconnected"),
        ConnectionResetError(104, "Connection reset by peer"),  # OSError subclass
    ],
)
async def test_post_dispatch_transport_error_not_retried_when_narrowed(exc):
    calls = {"n": 0}

    async def _op():
        calls["n"] += 1
        raise exc

    with pytest.raises(type(exc)):
        await retry_on_transient_agentbox_error(
            _op,
            max_attempts=5,
            retryable_transport_errors=CONNECT_PHASE_TRANSPORT_ERRORS,
        )
    # Surfaces immediately — the request may have already executed.
    assert calls["n"] == 1


async def test_connect_phase_error_still_retried_when_narrowed():
    calls = {"n": 0}

    async def _op():
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("Connection refused")  # request never delivered
        return "ok"

    result = await retry_on_transient_agentbox_error(
        _op,
        max_attempts=3,
        retryable_transport_errors=CONNECT_PHASE_TRANSPORT_ERRORS,
    )
    assert result == "ok"
    assert calls["n"] == 2


async def test_default_transport_set_still_retries_read_error():
    # The default (async-job / non-narrowed) path keeps the full set.
    calls = {"n": 0}

    async def _op():
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadError("blip")
        return "ok"

    assert await retry_on_transient_agentbox_error(_op, max_attempts=3) == "ok"
    assert calls["n"] == 2
