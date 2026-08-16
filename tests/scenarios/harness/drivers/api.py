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


class ApiDriver:
    """An authenticated HTTP client for one person."""

    def __init__(self, client: httpx.AsyncClient, *, token: str | None = None) -> None:
        self._client = client
        self._token = token

    @property
    def token(self) -> str | None:
        return self._token

    def authenticate(self, token: str) -> None:
        self._token = token

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {**self._headers, **kwargs.pop("headers", {})}
        return await self._client.request(method, path, headers=headers, **kwargs)

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
