"""Speak to Lemma over HTTP, the way any client does.

This is the only place in the suite that knows about paths, verbs, status codes
or JSON envelopes. Steps say ``creates_a_pod``; this says ``POST /pods``. That
separation is what will let the same scenarios run through the CLI and both
SDKs later — a second driver implements the same surface and the journeys do not
change.

Two conventions worth knowing:

* ``call`` returns the response untouched, so a step can assert on a refusal.
  ``expect`` raises a readable failure unless the status is what was wanted, and
  returns the decoded body. Steps use ``expect`` for the happy path and ``call``
  when the refusal *is* the point.
* Failures carry the response body. A bare ``assert response.status_code == 201``
  tells you a number; nearly every wasted hour debugging an API test is spent
  finding out what the body said.
"""

from __future__ import annotations

from typing import Any

import httpx

JSON = dict[str, Any]


class UnexpectedResponse(AssertionError):
    """The API answered something the scenario was not prepared for."""


def _reachable(path: str) -> str:
    """Route an absolute URL back to the server actually listening.

    The stack tells the backend its public address is
    `harness.stack.PUBLIC_API_URL`, because surfaces will not connect without a
    public HTTPS one. Absolute URLs the product generates — a signed bundle
    download, a share link — therefore point at a host that does not resolve.

    Rewriting to a path lets httpx resolve against the real base URL. What is
    being tested is unaffected: the product built the URL, and the same request
    reaches the same handler. Only the hostname is a fiction, and it is *our*
    fiction.
    """
    from harness.stack import PUBLIC_API_URL

    if path.startswith(PUBLIC_API_URL):
        remainder = path[len(PUBLIC_API_URL) :]
        return remainder if remainder.startswith("/") else f"/{remainder}"
    return path


class ApiDriver:
    """An authenticated HTTP client for one person."""

    def __init__(self, client: httpx.AsyncClient, *, token: str | None = None) -> None:
        self._client = client
        self._token = token

    @property
    def token(self) -> str | None:
        return self._token

    @property
    def base_url(self) -> str:
        """Where this client is really talking, for anything httpx cannot carry.

        A websocket handshake is not an httpx request, so a scenario watching
        records live has to build its own URL — and it must be the address the
        server is actually listening on, not the public-looking one the stack
        claims. See `PUBLIC_API_URL`.
        """
        return str(self._client.base_url).rstrip("/")

    def authenticate(self, token: str) -> None:
        self._token = token

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {**self._headers, **kwargs.pop("headers", {})}
        return await self._client.request(
            method, _reachable(path), headers=headers, **kwargs
        )

    async def expect(
        self,
        method: str,
        path: str,
        *,
        status: int | tuple[int, ...] = (200, 201),
        what: str = "",
        **kwargs: Any,
    ) -> Any:
        wanted = (status,) if isinstance(status, int) else status
        response = await self.call(method, path, **kwargs)
        if response.status_code not in wanted:
            raise UnexpectedResponse(
                f"{what or f'{method} {path}'} answered {response.status_code}, "
                f"expected {' or '.join(str(code) for code in wanted)}.\n"
                f"  {method} {path}\n"
                f"  body: {response.text[:2000]}"
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    async def opens_stream(
        self, path: str, *, timeout: float = 15.0, **kwargs: Any
    ) -> tuple[int, str, str]:
        """Open a server-sent event stream, take what it says first, and close.

        Reading a stream with an ordinary request waits for the body to end —
        and a live stream does not end, so the request times out and the
        scenario reports a hang where the product is working exactly as
        intended. This takes the headers and the first chunk, which is what a
        watcher actually sees, and then lets go.

        Returns ``(status, content_type, first_chunk)``. The chunk may be empty
        and that is not a failure: a stream opened after its run has already
        finished has nothing left to say. What a scenario can rely on is that
        the stream *opened*, and opened as an event stream.
        """
        headers = {
            "Accept": "text/event-stream",
            **self._headers,
            **kwargs.pop("headers", {}),
        }
        request = self._client.build_request(
            "GET", _reachable(path), headers=headers, timeout=timeout, **kwargs
        )
        response = await self._client.send(request, stream=True)
        try:
            first = ""
            async for chunk in response.aiter_text():
                first = chunk
                if chunk.strip():
                    break
            return (
                response.status_code,
                response.headers.get("content-type", ""),
                first,
            )
        finally:
            await response.aclose()

    # Convenience wrappers; every step goes through one of these.
    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.expect("GET", path, status=200, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.expect("POST", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> Any:
        return await self.expect("PATCH", path, status=200, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> Any:
        return await self.expect("PUT", path, status=(200, 201, 204), **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> Any:
        return await self.expect("DELETE", path, status=(200, 202, 204), **kwargs)


def items_of(payload: Any) -> list[JSON]:
    """Rows out of a list response, whichever envelope it uses.

    Some list endpoints return a bare array and some return ``{"items": [...]}``.
    A scenario should not have to care which, and should certainly not break
    when one of them changes.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []
