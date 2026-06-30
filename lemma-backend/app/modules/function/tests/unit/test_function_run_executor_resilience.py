"""Unit tests for the function-run executor's resilience behaviour:

* Fix 1 (outer): a synchronous (non-idempotent) execute does not re-run the
  whole function on an ambiguous post-dispatch error, but still recovers from a
  "request provably never ran" error.
* Fix 3: backend<->sandbox failures surface a clean, user-facing ``run.error``
  with no server tracebacks / raw HTTP bodies / ``Errno`` strings.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.log import log as _log_module  # noqa: F401  (ensure logging import OK)
from app.modules.function.application import function_run_executor as fre
from app.modules.function.application.function_run_executor import (
    _NON_IDEMPOTENT_RECOVERABLE_SANDBOX_STATUS_CODES,
    _NON_IDEMPOTENT_RECOVERABLE_SANDBOX_TRANSPORT_ERRORS,
    FunctionRunExecutor,
)
from app.modules.function.domain.errors import FunctionValidationError

pytestmark = pytest.mark.asyncio


def _executor() -> FunctionRunExecutor:
    return FunctionRunExecutor(workspace_service=None, storage_factory=None)


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://sandbox.test/execute")
    response = httpx.Response(status_code, request=request, text="internal stack trace leak")
    return httpx.HTTPStatusError("error", request=request, response=response)


class _FakeRun:
    id = "run-1"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _instant(_delay):
        return None

    monkeypatch.setattr(fre.asyncio, "sleep", _instant)


# --------------------------------------------------------------------------
# Fix 3 — clean, user-facing error mapping (no server internals)
# --------------------------------------------------------------------------

_LEAK_SUBSTRINGS = ("Traceback", "Errno", "104", "Connection reset", "stack trace", "sandbox.test")


@pytest.mark.parametrize(
    "exc,expected_fragment",
    [
        (ConnectionResetError(104, "Connection reset by peer"), "interrupted"),
        (httpx.ReadError("boom"), "interrupted"),
        (httpx.RemoteProtocolError("server disconnected"), "interrupted"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (TimeoutError("deadline"), "timeout"),
        (_http_status_error(503), "temporarily unavailable"),
        (_http_status_error(500), "unexpected error"),
        (ValueError("kaboom internal"), "internal error"),
    ],
)
async def test_user_facing_error_is_clean(exc, expected_fragment):
    msg = _executor()._user_facing_execution_error(exc)
    assert expected_fragment in msg.lower()
    for leak in _LEAK_SUBSTRINGS:
        assert leak not in msg


async def test_function_validation_error_message_passes_through():
    msg = _executor()._user_facing_execution_error(
        FunctionValidationError("Input does not match the declared schema.")
    )
    assert msg == "Input does not match the declared schema."


# --------------------------------------------------------------------------
# Fix 1 (outer) — sandbox-recovery is idempotency-aware for sync executes
# --------------------------------------------------------------------------


async def test_is_recoverable_matrix_default_vs_narrowed():
    # Default (async-job / JOB path): the full set still recovers read errors + 5xx.
    assert FunctionRunExecutor._is_recoverable_sandbox_error(httpx.ReadError("x")) is True
    assert FunctionRunExecutor._is_recoverable_sandbox_error(_http_status_error(500)) is True

    # Narrowed (sync / non-idempotent): only "request provably never ran" errors.
    narrow = dict(
        status_codes=_NON_IDEMPOTENT_RECOVERABLE_SANDBOX_STATUS_CODES,
        transport_errors=_NON_IDEMPOTENT_RECOVERABLE_SANDBOX_TRANSPORT_ERRORS,
    )
    assert FunctionRunExecutor._is_recoverable_sandbox_error(httpx.ReadError("x"), **narrow) is False
    assert FunctionRunExecutor._is_recoverable_sandbox_error(_http_status_error(500), **narrow) is False
    assert FunctionRunExecutor._is_recoverable_sandbox_error(httpx.ConnectError("x"), **narrow) is True
    assert FunctionRunExecutor._is_recoverable_sandbox_error(_http_status_error(404), **narrow) is True


async def test_sync_recovery_does_not_rerun_on_post_dispatch_error():
    calls = {"n": 0}

    async def _attempt():
        calls["n"] += 1
        raise httpx.ReadError("response-leg failure after the function ran")

    with pytest.raises(httpx.ReadError):
        await _executor()._execute_with_sandbox_recovery(
            run=_FakeRun(),
            make_attempt=_attempt,
            recoverable_status_codes=_NON_IDEMPOTENT_RECOVERABLE_SANDBOX_STATUS_CODES,
            recoverable_transport_errors=_NON_IDEMPOTENT_RECOVERABLE_SANDBOX_TRANSPORT_ERRORS,
        )
    # No re-run — the function may already have executed its side effect.
    assert calls["n"] == 1


async def test_sync_recovery_still_recovers_when_request_never_ran():
    calls = {"n": 0}

    async def _attempt():
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("pod missing / connection refused")
        return "ok"

    result = await _executor()._execute_with_sandbox_recovery(
        run=_FakeRun(),
        make_attempt=_attempt,
        recoverable_status_codes=_NON_IDEMPOTENT_RECOVERABLE_SANDBOX_STATUS_CODES,
        recoverable_transport_errors=_NON_IDEMPOTENT_RECOVERABLE_SANDBOX_TRANSPORT_ERRORS,
    )
    assert result == "ok"
    assert calls["n"] == 2


async def test_default_recovery_reruns_read_error_for_job_path():
    # The JOB/async path keeps the full recoverable set (re-running an accepted
    # job is safe because the sandbox dedupes by run_id).
    calls = {"n": 0}

    async def _attempt():
        calls["n"] += 1
        raise httpx.ReadError("blip")

    with pytest.raises(httpx.ReadError):
        await _executor()._execute_with_sandbox_recovery(
            run=_FakeRun(), make_attempt=_attempt
        )
    assert calls["n"] == fre._SANDBOX_RECOVERY_MAX_ATTEMPTS
