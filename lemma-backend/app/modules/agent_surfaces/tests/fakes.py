"""Doubles for agent_surfaces tests that stand in at a port, not on a name.

`platform_http` answers the platform calls a test makes by a handler, through
the same seam production uses (`bind_outbound_client`), so the request a
platform client builds -- URL, headers, body -- is the one the test sees.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import HTTP

import httpx

from app.modules.agent_surfaces.platforms.outbound_http import bind_outbound_client

type PlatformHandler = Callable[
    [httpx.Request], httpx.Response | Awaitable[httpx.Response]
]


class PlatformHttp:
    """Every request a test's platform calls sent, in order."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def to(self, fragment: str) -> list[httpx.Request]:
        """The requests whose URL contains ``fragment``."""
        return [request for request in self.requests if fragment in str(request.url)]

    @staticmethod
    def json_of(request: httpx.Request) -> dict[str, object]:
        """A JSON request body, which every platform call here sends as an
        object."""
        body = json.loads(request.content or b"{}")
        assert isinstance(body, dict)
        return body

    @staticmethod
    def form_of(request: httpx.Request) -> dict[str, FormPart]:
        """A multipart request body, by field name."""
        head = (
            b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n"
        )
        message = BytesParser(policy=HTTP).parsebytes(head + request.content)
        parts: dict[str, FormPart] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            payload = part.get_payload(decode=True)
            parts[str(name)] = FormPart(
                filename=part.get_filename(),
                content_type=part.get_content_type(),
                content=payload if isinstance(payload, bytes) else b"",
            )
        return parts


@dataclass(frozen=True)
class FormPart:
    filename: str | None
    content_type: str
    content: bytes

    @property
    def text(self) -> str:
        return self.content.decode()


@contextmanager
def platform_http(handler: PlatformHandler) -> Iterator[PlatformHttp]:
    """Answer every platform call in this block with ``handler``."""
    recorded = PlatformHttp()

    async def record(request: httpx.Request) -> httpx.Response:
        recorded.requests.append(request)
        answer = handler(request)
        if isinstance(answer, httpx.Response):
            return answer
        return await answer

    with bind_outbound_client(httpx.AsyncClient(transport=httpx.MockTransport(record))):
        yield recorded


def answer_json(payload: object, *, status_code: int = 200) -> PlatformHandler:
    """A handler answering every request with one JSON body."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status_code, json=payload)

    return handler
