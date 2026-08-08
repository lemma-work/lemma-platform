from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import httpx
import pytest

from sandbox_runtime.function.runner import GatewayClient
from sandbox_runtime.function.runtime_models import TerminalReport
from sandbox_runtime.function.trace_context import bind_trace_context


pytestmark = pytest.mark.asyncio


def _deadline() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=30)


def _report() -> TerminalReport:
    return TerminalReport(
        status="completed",
        output_data={"answer": 42},
        stdout="done",
        stderr="",
    )


async def test_artifact_uses_same_revision_scoped_function_token():
    observed: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed
        observed = request
        return httpx.Response(200, content=b"artifact", request=request)

    function_id = uuid4()
    revision_hash = f"sha256:{'b' * 64}"
    gateway = GatewayClient(
        "https://gateway.lemma.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        artifact = await gateway.artifact(
            "delegated-function-token",
            function_id=function_id,
            revision_hash=revision_hash,
        )
    finally:
        await gateway.close()

    assert artifact == b"artifact"
    assert observed is not None
    assert observed.url.path == (
        f"/internal/function-runtime/functions/{function_id}/artifacts/{revision_hash}"
    )
    assert observed.headers["Authorization"] == "Bearer delegated-function-token"
    assert "If-Match" not in observed.headers


async def test_terminal_callback_retries_identical_payload_after_lost_response():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadError("response lost after commit", request=request)
        return httpx.Response(
            200,
            json={"accepted": True, "duplicate": True},
            request=request,
        )

    run_id = uuid4()
    gateway = GatewayClient(
        "https://gateway.lemma.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        await gateway.terminal(
            "delegated-function-token",
            run_id=run_id,
            deadline_at=_deadline(),
            report=_report(),
        )
    finally:
        await gateway.close()

    assert len(requests) == 2
    assert requests[0].url == requests[1].url
    assert requests[0].headers["Authorization"] == (
        "Bearer delegated-function-token"
    )
    assert json.loads(requests[0].content) == json.loads(requests[1].content)


async def test_terminal_callback_retries_transient_status_and_honors_retry_after():
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                503,
                headers={"Retry-After": "0.05"},
                request=request,
            )
        return httpx.Response(200, json={"accepted": True}, request=request)

    gateway = GatewayClient(
        "https://gateway.lemma.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        await gateway.terminal(
            "delegated-function-token",
            run_id=uuid4(),
            deadline_at=_deadline(),
            report=_report(),
        )
    finally:
        await gateway.close()

    assert attempts == 2


async def test_terminal_callback_does_not_retry_state_or_credential_rejection():
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(409, json={"detail": "terminal run"}, request=request)

    gateway = GatewayClient(
        "https://gateway.lemma.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await gateway.terminal(
                "delegated-function-token",
                run_id=uuid4(),
                deadline_at=_deadline(),
                report=_report(),
            )
    finally:
        await gateway.close()

    assert attempts == 1


async def test_terminal_callback_does_not_attempt_after_retry_deadline():
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"accepted": True}, request=request)

    gateway = GatewayClient(
        "https://gateway.lemma.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(
            TimeoutError,
            match="terminal callback retry deadline elapsed",
        ):
            await gateway.terminal(
                "delegated-function-token",
                run_id=uuid4(),
                deadline_at=datetime.now(timezone.utc) - timedelta(seconds=61),
                report=_report(),
            )
    finally:
        await gateway.close()

    assert attempts == 0


async def test_gateway_callbacks_forward_current_w3c_trace_context():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"accepted": True}, request=request)

    gateway = GatewayClient(
        "https://gateway.lemma.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        with bind_trace_context(
            {"traceparent": ("00-1234567890abcdef1234567890abcdef-1234567890abcdef-01")}
        ):
            await gateway.terminal(
                "delegated-function-token",
                run_id=uuid4(),
                deadline_at=_deadline(),
                report=_report(),
            )
    finally:
        await gateway.close()

    assert requests[0].headers["traceparent"] == (
        "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"
    )
