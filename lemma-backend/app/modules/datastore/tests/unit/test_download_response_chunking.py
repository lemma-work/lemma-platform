"""A download already in memory must go out as one response, not one per newline.

``StreamingResponse(BytesIO(content))`` reads as harmless and is not. Iterating
a ``BytesIO`` yields one **line** at a time, so a binary body becomes one ASGI
``http.response.body`` message per ``0x0A`` byte it happens to contain. Against
dev that ran at a flat ~3,750 chunks/second whatever the chunk size, which made
download latency a function of a file's newline count rather than its size — a
2.1MB PDF containing 51,571 newlines took 13.8 seconds, while a 6.4MB PDF with
28,778 took 7.6. Reading the same objects out of storage took 40ms.

Counted in ASGI messages rather than timed, deliberately. The defect is a
message per newline; wall clock is how it was noticed, not what it is, and a
timing assertion on CI hardware would be both flakier and less specific.
"""

from __future__ import annotations

import anyio
import pytest

from app.modules.datastore.api.file_download_response import (
    build_child_download_response,
    build_original_download_response,
)

pytestmark = pytest.mark.unit


class _Entity:
    """The fields the original-download builder reads."""

    def __init__(self, name: str, content_type: str, sha: str | None) -> None:
        self.name = name
        self.content_type = content_type
        self.content_sha256 = sha


class _Download:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.not_modified = False


def _pdf_like(newlines: int) -> bytes:
    """A binary body with a known number of newline bytes, like a real PDF."""
    return b"%PDF-1.4\n" + b"".join(b"x" * 64 + b"\n" for _ in range(newlines - 1))


async def _body_messages(response) -> list[bytes]:
    """Drive the response through ASGI and collect its body messages.

    ``receive`` reports the request body once and then blocks, which is what a
    real server does: the disconnect message only arrives if the client
    actually goes away. Both of the obvious shortcuts break the measurement in
    opposite directions -- returning ``http.request`` forever livelocks
    ``StreamingResponse``'s disconnect watcher, and returning
    ``http.disconnect`` straight away makes it abandon the stream before
    sending anything, so the pre-fix behaviour reads as zero messages instead
    of one per line. Starlette cancels the watcher once the body is done, so
    blocking here terminates normally.
    """
    sent: list[dict] = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await anyio.sleep_forever()

    async def send(message):
        sent.append(message)

    await response({"type": "http", "method": "GET", "headers": []}, receive, send)
    return [
        m["body"] for m in sent if m["type"] == "http.response.body" and m.get("body")
    ]


@pytest.mark.parametrize("newlines", [1, 500, 20_000])
async def test_an_original_download_is_one_body_message(newlines: int) -> None:
    content = _pdf_like(newlines)
    response = build_original_download_response(
        _Entity("paper.pdf", "application/pdf", "abc123"), _Download(content)
    )

    bodies = await _body_messages(response)

    assert len(bodies) == 1, (
        f"a {len(content)}-byte body containing {newlines} newlines was sent as "
        f"{len(bodies)} ASGI messages; it is being iterated per line, which "
        "makes download time scale with newline count rather than size"
    )
    assert b"".join(bodies) == content, "the body was altered in transit"


async def test_a_child_artifact_download_is_one_body_message() -> None:
    """The derived-artifact path had the same shape and the same fix."""
    content = _pdf_like(20_000)
    response = build_child_download_response(
        request_if_none_match=None,
        artifact_name="document.md",
        content=content,
        content_type="text/markdown",
    )

    bodies = await _body_messages(response)

    assert len(bodies) == 1, f"a child artifact was sent as {len(bodies)} ASGI messages"
    assert b"".join(bodies) == content


async def test_the_client_is_told_how_many_bytes_to_expect() -> None:
    """Content-Length is the other thing the streaming form gave up.

    Without it the transfer is chunked and of unknown size, so a client cannot
    show progress or size-check before reading.
    """
    content = _pdf_like(1_000)
    response = build_original_download_response(
        _Entity("paper.pdf", "application/pdf", "abc123"), _Download(content)
    )

    assert response.headers.get("content-length") == str(len(content)), (
        f"content-length is {response.headers.get('content-length')!r} for a "
        f"{len(content)}-byte body"
    )
