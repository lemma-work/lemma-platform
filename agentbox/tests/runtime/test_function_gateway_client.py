from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import httpx
import pytest

from agentbox.function_runtime.runner import GatewayClient
from agentbox.function_runtime.runtime_models import (
    AttemptClaim,
    RuntimeIdentity,
    TerminalReport,
)


pytestmark = pytest.mark.asyncio


def _claim() -> AttemptClaim:
    return AttemptClaim(
        attempt_id=uuid4(),
        fence=7,
        runtime_token="runtime-token-" + "x" * 32,
        artifact_url="/artifact",
        artifact_sha256=f"sha256:{'a' * 64}",
        input_data={},
        config=None,
        identity=RuntimeIdentity(
            user_id=uuid4(),
            pod_id=uuid4(),
            function_id=uuid4(),
            function_name="probe",
        ),
        lemma_token="lemma-token",
        lemma_base_url="https://api.lemma.test",
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )


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

    claim = _claim()
    report = TerminalReport(
        fence=claim.fence,
        status="completed",
        output_data={"answer": 42},
        stdout="done",
        stderr="",
    )
    gateway = GatewayClient(
        "https://gateway.lemma.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        await gateway.terminal(claim, report)
    finally:
        await gateway.close()

    assert len(requests) == 2
    assert requests[0].url == requests[1].url
    assert requests[0].headers["Authorization"] == requests[1].headers["Authorization"]
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

    claim = _claim()
    gateway = GatewayClient(
        "https://gateway.lemma.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        await gateway.started(claim)
    finally:
        await gateway.close()

    assert attempts == 2


async def test_terminal_callback_does_not_retry_fence_or_credential_rejection():
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(409, json={"detail": "stale fence"}, request=request)

    claim = _claim()
    gateway = GatewayClient(
        "https://gateway.lemma.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await gateway.started(claim)
    finally:
        await gateway.close()

    assert attempts == 1
