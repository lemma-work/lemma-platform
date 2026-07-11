from __future__ import annotations

from collections import deque

import pytest
from starlette.requests import Request

from app.core.api.streaming_multipart import (
    MultipartFileLimit,
    UploadStagingCoordinator,
    stream_multipart_form,
)
from app.core.domain.errors import PayloadTooLargeError, UploadCapacityExceededError


def _request(chunks: list[bytes], boundary: str = "lemma-boundary"):
    messages = deque(
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    )
    reads = 0

    async def receive():
        nonlocal reads
        reads += 1
        return messages.popleft()

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/upload",
            "headers": [
                (
                    b"content-type",
                    f"multipart/form-data; boundary={boundary}".encode(),
                )
            ],
        },
        receive,
    )
    return request, lambda: reads


def _file_body(content: bytes, boundary: str = "lemma-boundary") -> bytes:
    return (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="sample.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()


@pytest.mark.asyncio
async def test_streaming_multipart_stages_and_cleans_file() -> None:
    request, _ = _request([_file_body(b"hello")])
    staged_path = None

    async with stream_multipart_form(
        request,
        file_limits={"file": MultipartFileLimit(max_bytes=5, required=True)},
        combined_max_bytes=5,
    ) as form:
        upload = form.require_file("file")
        staged_path = upload.path
        assert await upload.read_bytes() == b"hello"
        assert upload.staged.sha256
        assert staged_path.exists()

    assert staged_path is not None and not staged_path.exists()


@pytest.mark.asyncio
async def test_field_limit_stops_reading_remaining_request_chunks() -> None:
    body = _file_body(b"0123456789")
    split = body.index(b"0123456789") + 6
    request, reads = _request([body[:split], body[split:]])

    with pytest.raises(PayloadTooLargeError):
        async with stream_multipart_form(
            request,
            file_limits={"file": MultipartFileLimit(max_bytes=5, required=True)},
            combined_max_bytes=100,
        ):
            pass

    assert reads() == 1


@pytest.mark.asyncio
async def test_process_capacity_fails_fast_without_reading_body() -> None:
    request, reads = _request([_file_body(b"x")])
    coordinator = UploadStagingCoordinator(max_requests=0, max_active_bytes=1)

    with pytest.raises(UploadCapacityExceededError):
        async with stream_multipart_form(
            request,
            file_limits={"file": MultipartFileLimit(max_bytes=1)},
            combined_max_bytes=1,
            coordinator=coordinator,
        ):
            pass

    assert reads() == 0
