from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.modules.agent.api.controllers.agent_host_transfer_controller as controller
from app.modules.agent.infrastructure.agent_host.transfers import (
    TransferRefusal,
    TransferRefused,
)


pytestmark = pytest.mark.asyncio


def _request(chunks: list[bytes], *, content_length: str | None = None) -> Request:
    index = 0

    async def receive():
        nonlocal index
        current = index
        index += 1
        if current >= len(chunks):
            return {"type": "http.disconnect"}
        return {
            "type": "http.request",
            "body": chunks[current],
            "more_body": current < len(chunks) - 1,
        }

    headers = (
        [(b"content-length", content_length.encode("latin-1"))]
        if content_length is not None
        else []
    )
    return Request(
        {"type": "http", "method": "PUT", "path": "/agent-host/transfers/test", "headers": headers},
        receive,
    )


async def test_agent_host_body_rejects_oversized_chunk_before_extending(monkeypatch):
    class TrackingBuffer(bytearray):
        extend_calls = 0

        def extend(self, value):
            self.extend_calls += 1
            return super().extend(value)

    monkeypatch.setattr(controller, "bytearray", TrackingBuffer, raising=False)

    with pytest.raises(TransferRefused) as rejected:
        await controller._body(_request([b"x" * 9]), 8)

    assert rejected.value.refusal is TransferRefusal.TOO_LARGE
    assert TrackingBuffer.extend_calls == 0


@pytest.mark.parametrize("content_length", ["²", "+8", "8x", ""])
async def test_agent_host_body_rejects_malformed_content_length(content_length):
    with pytest.raises(HTTPException) as rejected:
        await controller._body(_request([b"{}"], content_length=content_length), 8)

    assert rejected.value.status_code == 400


async def test_agent_host_body_accepts_exact_limit():
    assert await controller._body(_request([b"1234", b"5678"]), 8) == b"12345678"
