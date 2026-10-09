"""`events/*` on a connected MCP server, as raw JSON-RPC.

Not through fastmcp's client: the draft's methods are in no SDK, and they are
offered only on revision 2026-07-28, which carries the protocol version and the
method on every request -- in headers and in `_meta` -- with no handshake to
negotiate it. One POST per call is the whole transport.

The server is the tenant's, so every call goes through the SSRF guard the
executor uses, redirects are refused rather than followed, and a server that
answers in a shape this does not recognise is a refusal, not a crash.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable

import httpx
from pydantic import JsonValue, SecretStr

from app.core.net.http_client import get_shared_http_client
from app.core.net.url_guard import UnsafeUrlError, assert_safe_url, request_guarded
from app.modules.connectors.domain.mcp_events import MCP_EVENTS_PROTOCOL

CALL_TIMEOUT = httpx.Timeout(20.0, connect=5.0)

#: The JSON-RPC code this side reports for a failure that is not the server's
#: answer: unreachable, refused by the guard, unreadable.
TRANSPORT_FAILURE = -32603


class McpEventsError(Exception):
    """The server refused, or could not be asked."""

    def __init__(
        self, code: int, message: str, data: dict[str, JsonValue] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}


class McpEventsClient:
    def __init__(
        self,
        server_url: str,
        headers: dict[str, str],
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._server_url = server_url
        self._headers = headers
        self._http = http

    async def list_events(self) -> dict[str, JsonValue]:
        return await self._call("events/list", {})

    async def subscribe(
        self,
        *,
        name: str,
        arguments: dict[str, JsonValue],
        url: str,
        secret: SecretStr,
        ttl_ms: int,
    ) -> dict[str, JsonValue]:
        return await self._call(
            "events/subscribe",
            {
                "name": name,
                "arguments": arguments,
                "delivery": {
                    "mode": "webhook",
                    "url": url,
                    "secret": secret.get_secret_value(),
                },
                "ttlMs": ttl_ms,
            },
        )

    async def unsubscribe(
        self, *, name: str, arguments: dict[str, JsonValue], url: str
    ) -> None:
        await self._call(
            "events/unsubscribe",
            {
                "name": name,
                "arguments": arguments,
                "delivery": {"mode": "webhook", "url": url},
            },
        )

    async def _call(
        self, method: str, params: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        request_id = secrets.token_hex(6)
        envelope = dict(params)
        envelope["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": MCP_EVENTS_PROTOCOL,
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        try:
            await assert_safe_url(self._server_url)
            response = await request_guarded(
                self._http or get_shared_http_client(),
                "POST",
                self._server_url,
                headers={
                    **self._headers,
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": MCP_EVENTS_PROTOCOL,
                    "Mcp-Method": method,
                },
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": envelope,
                },
                follow_redirects=False,
                timeout=CALL_TIMEOUT,
            )
        except UnsafeUrlError as exc:
            raise McpEventsError(
                TRANSPORT_FAILURE, "unsafe_url", {"reason": exc.reason}
            ) from exc
        except httpx.HTTPError as exc:
            raise McpEventsError(TRANSPORT_FAILURE, type(exc).__name__) from exc
        return _result_of(response, request_id)


def _result_of(response: httpx.Response, request_id: str) -> dict[str, JsonValue]:
    message = _message_in(response, lambda found: found.get("id") == request_id)
    if message is None:
        raise McpEventsError(
            TRANSPORT_FAILURE, f"http_{response.status_code}", {"unreadable": True}
        )
    error = message.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        data = error.get("data")
        raise McpEventsError(
            code if isinstance(code, int) else TRANSPORT_FAILURE,
            str(error.get("message") or "refused"),
            data if isinstance(data, dict) else None,
        )
    result = message.get("result")
    return result if isinstance(result, dict) else {}


def _message_in(
    response: httpx.Response, wanted: Callable[[dict[str, JsonValue]], bool]
) -> dict[str, JsonValue] | None:
    """The JSON-RPC answer in a JSON body or an event stream, or None."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        candidates = [
            line.removeprefix("data:").strip()
            for line in response.text.splitlines()
            if line.startswith("data:")
        ]
    else:
        candidates = [response.text]
    for raw in candidates:
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        if isinstance(parsed, dict) and wanted(parsed):
            return parsed
    return None
