from __future__ import annotations

import pytest
from starlette.requests import Request
from starlette.responses import Response

import app.modules.mcp_access.api.oauth_routes as oauth_routes


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
        {
            "type": "http",
            "method": "POST",
            "path": "/oauth/register",
            "headers": headers,
        },
        receive,
    )


async def test_oauth_body_rejects_oversized_chunk_before_extending(monkeypatch):
    instances = []

    class TrackingBuffer(bytearray):
        def __init__(self, *args):
            super().__init__(*args)
            self.extend_calls = 0
            instances.append(self)

        def extend(self, value):
            self.extend_calls += 1
            return super().extend(value)

    monkeypatch.setattr(oauth_routes, "bytearray", TrackingBuffer, raising=False)
    called = []

    async def handler(request):
        called.append(True)
        return Response(status_code=204)

    response = await oauth_routes._bounded(handler)(
        _request([b"x" * (oauth_routes.MAX_BODY_BYTES + 1)])
    )

    assert response.status_code == 413
    assert called == []
    assert instances[0].extend_calls == 0


@pytest.mark.parametrize("content_length", ["²", "+1", "1.0", ""])
async def test_oauth_body_rejects_malformed_content_length(content_length):
    async def handler(request):
        return Response(status_code=204)

    response = await oauth_routes._bounded(handler)(
        _request([b"{}"], content_length=content_length)
    )

    assert response.status_code == 400


async def test_oauth_body_accepts_exact_limit_and_passes_buffered_body():
    expected = b"x" * oauth_routes.MAX_BODY_BYTES

    async def handler(request):
        return Response(content=await request.body())

    response = await oauth_routes._bounded(handler)(
        _request([expected[:17], expected[17:]])
    )

    assert response.status_code == 200
    assert response.body == expected
