"""Posting to a route the way the server does, and counting what it read.

A body ceiling on an unauthenticated route is a claim about how much of the
body the route pulls off the connection before it refuses. An HTTP client hides
exactly that: it hands the whole body over and reports a status. This drives
the app over raw ASGI -- one ``http.request`` message per chunk, as uvicorn
delivers them -- and counts the chunks the app asked for, so "refused before
reading" and "stopped at the chunk that crossed" are ordinary assertions.

The app is built behind the real ``RequestBodyLimitMiddleware`` with the real
error handlers, at a global ceiling far above any route's. A route's 413 has to
come through as the route's own ``max_bytes``; one that escaped to the
middleware would come back with the global figure instead.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from fastapi import APIRouter, FastAPI
from starlette.types import ASGIApp, Message

from app.core.api.exception_handlers import register_exception_handlers
from app.middleware import RequestBodyLimitMiddleware

#: Carried on every delivery so the 413 envelope can be compared whole.
REQUEST_ID = "body-ceiling-test"

GLOBAL_LIMIT_BYTES = 64 * 1024 * 1024

CHUNK_BYTES = 64 * 1024


def app_with(
    router: APIRouter,
    overrides: Mapping[Callable[..., object], Callable[..., object]] | None = None,
) -> ASGIApp:
    """``router`` alone in an app, its collaborators replaced by ``overrides``."""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides.update(overrides or {})
    return RequestBodyLimitMiddleware(app, max_bytes=GLOBAL_LIMIT_BYTES)


@dataclass(frozen=True, slots=True)
class Delivered:
    status: int
    body: bytes
    #: How many of the chunks offered the app asked for before it answered.
    chunks_read: int

    def json(self) -> object:
        return json.loads(self.body)


def chunks_of(body: bytes) -> list[bytes]:
    return [body[i : i + CHUNK_BYTES] for i in range(0, len(body), CHUNK_BYTES)]


def past_ceiling(max_bytes: int) -> list[bytes]:
    """Two chunks more than ``max_bytes`` holds: one to cross it, one never read."""
    return [b"\0" * CHUNK_BYTES] * (max_bytes // CHUNK_BYTES + 2)


def chunks_to_cross(max_bytes: int) -> int:
    """How many ``past_ceiling`` chunks a route reads before it must refuse."""
    return max_bytes // CHUNK_BYTES + 1


def too_large(max_bytes: int) -> dict[str, object]:
    """``RequestBodyLimitMiddleware``'s 413 envelope, at ``max_bytes``."""
    return {
        "message": "request exceeds the maximum allowed size",
        "code": "UPLOAD_TOO_LARGE",
        "request_id": REQUEST_ID,
        "details": {"field": "request", "max_bytes": max_bytes},
    }


async def deliver(
    app: ASGIApp,
    path: str,
    chunks: Sequence[bytes],
    *,
    headers: Sequence[tuple[bytes, bytes]] = (),
    declared_length: int | None = None,
) -> Delivered:
    """POST ``chunks`` to ``path``: chunked with no length unless one is declared.

    The declared length is sent as given, true or not -- what a route may trust
    it for is part of what is under test.
    """
    sent = [(b"x-request-id", REQUEST_ID.encode()), *headers]
    if declared_length is None:
        sent.append((b"transfer-encoding", b"chunked"))
    else:
        sent.append((b"content-length", str(declared_length).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": sent,
        "client": ("127.0.0.1", 1234),
        "server": ("test", 443),
    }
    read = 0

    async def receive() -> Message:
        nonlocal read
        if read == len(chunks):
            return {"type": "http.disconnect"}
        read += 1
        return {
            "type": "http.request",
            "body": chunks[read - 1],
            "more_body": read < len(chunks),
        }

    replies: list[Message] = []

    async def send(message: Message) -> None:
        replies.append(message)

    await app(scope, receive, send)
    (start,) = (m for m in replies if m["type"] == "http.response.start")
    body = b"".join(
        m.get("body", b"") for m in replies if m["type"] != "http.response.start"
    )
    return Delivered(status=start["status"], body=body, chunks_read=read)
