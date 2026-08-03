"""MCP connector executor — proxy operations to an external MCP server's tools.

An ``mcp``-kind connector stores the MCP server URL on the auth-config
(``connection_config.server_url``) and the bearer token on the account
(``third_party_credentials``). Each operation's ``execution`` descriptor names the
tool (``tool_name``); executing calls ``tools/call`` on the server and maps the
result back to JSON (or a ``BinaryContentResult`` for binary content blocks).

The MCP client factory is injectable so tests can drive an in-memory server.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Callable

import httpx
from fastmcp.exceptions import FastMCPError, McpError

from lemma_connectors.core.results import BinaryContentResult

from app.core.log.log import get_logger
from app.modules.connectors.domain.errors import (
    OperationExecutionInfrastructureError,
    OperationExecutionValidationError,
)

logger = get_logger(__name__)

# What a call to a remote MCP server can realistically fail with: the server
# rejecting it (fastmcp/mcp), the connection failing (httpx/OSError), or our own
# deadline firing. Anything outside this set is a bug in this process and should
# surface as one rather than being reported as an upstream fault.
_MCP_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    FastMCPError,
    McpError,
    httpx.HTTPError,
    OSError,
    TimeoutError,
    ValueError,
)


def _is_transport_failure(exc: BaseException, *, depth: int = 0) -> bool:
    """Whether ``exc`` is the upstream failing rather than a bug in this process.

    fastmcp buries the real cause twice over. It runs its transport under an
    anyio task group, so a refusal arrives inside an ``ExceptionGroup``; and it
    re-raises connect failures as a bare ``RuntimeError`` with the original only
    attached as ``__cause__``. Matching on the raised type alone therefore misses
    both, which is how connection errors escaped as unhandled 500s instead of
    clean domain failures. Rather than catching ``RuntimeError`` wholesale --
    which would swallow our own bugs -- this follows the chain and only accepts
    it when a genuine transport error is underneath.
    """
    if depth > 5:
        return False
    if isinstance(exc, BaseExceptionGroup):
        return bool(exc.exceptions) and all(
            _is_transport_failure(inner, depth=depth + 1) for inner in exc.exceptions
        )
    if isinstance(exc, _MCP_TRANSPORT_ERRORS):
        return True
    for nested in (exc.__cause__, exc.__context__):
        if nested is not None and _is_transport_failure(nested, depth=depth + 1):
            return True
    return False


def _flatten_message(exc: BaseException) -> str:
    """Readable text for an error that may be a nested group."""
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(_flatten_message(inner) for inner in exc.exceptions)
    return str(exc)

# (server_url, headers) -> an async-context-manager MCP client exposing
# ``list_tools()`` and ``call_tool(name, args)``.
McpClientFactory = Callable[..., Any]


def default_mcp_client_factory(
    server_url: str, headers: dict[str, str], timeout: float | None = None
) -> Any:
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    # fastmcp has no timeout of its own, so an unresponsive server would
    # otherwise hang whichever request drove it -- including discovery, which
    # runs inline while creating an install.
    return Client(
        StreamableHttpTransport(url=server_url, headers=headers), timeout=timeout
    )


def build_mcp_headers(
    connection_config: dict[str, Any] | None, credentials: dict[str, Any] | None
) -> dict[str, str]:
    headers = dict((connection_config or {}).get("extra_headers") or {})
    creds = credentials or {}
    token = (
        creds.get("bearer_token")
        or creds.get("access_token")
        or (connection_config or {}).get("bearer_token")
    )
    if token:
        headers.setdefault("Authorization", f"Bearer {token}")
    return headers


class McpExecutor:
    def __init__(self, client_factory: McpClientFactory | None = None):
        self._client_factory = client_factory or default_mcp_client_factory

    async def execute(
        self,
        *,
        connector_id: str,
        operation_name: str,
        execution: dict[str, Any],
        payload: dict[str, Any],
        third_party_credentials: dict[str, Any] | None,
        connection_config: dict[str, Any] | None = None,
        deadline_seconds: float | None = None,
    ) -> Any:
        server_url = (connection_config or {}).get("server_url")
        if not server_url:
            raise OperationExecutionValidationError(
                "MCP connection requires 'server_url'.",
                details={"reason": "missing_server_url"},
            )
        tool_name = (execution or {}).get("tool_name") or operation_name
        headers = build_mcp_headers(connection_config, third_party_credentials)

        logger.debug(
            "connectors.mcp_executor.calling_mcp_tool.observed",
            connector_id=connector_id,
            tool_name=tool_name,
        )
        try:
            client = self._client_factory(server_url, headers, deadline_seconds)
            async with client:
                result = await client.call_tool(tool_name, payload or {})
        except (OperationExecutionValidationError, OperationExecutionInfrastructureError):
            raise
        except (*_MCP_TRANSPORT_ERRORS, BaseExceptionGroup, RuntimeError) as exc:
            if not _is_transport_failure(exc):
                # A group carrying something we do not recognise is a bug in this
                # process, not an upstream fault; let it surface as itself.
                raise
            message = _flatten_message(exc)
            raise OperationExecutionInfrastructureError(
                f"MCP tool '{tool_name}' failed: {message}",
                details={"provider": "mcp", "upstream_message": message},
            ) from exc

        return self._map_result(tool_name, result)

    def _map_result(self, tool_name: str, result: Any) -> Any:
        if getattr(result, "is_error", False):
            text = _collect_text(getattr(result, "content", None) or [])
            raise OperationExecutionInfrastructureError(
                f"MCP tool '{tool_name}' returned an error: {text}",
                details={"provider": "mcp"},
            )
        structured = _structured_output(result)
        if structured is not None:
            return structured
        return _map_content_blocks(_content_blocks(result))


def _collect_text(blocks: Any) -> str:
    parts = []
    for block in blocks or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return " ".join(parts)


def _structured_output(result: Any) -> Any | None:
    """Prefer whatever typed output the server already produced."""
    data = getattr(result, "data", None)
    if data is not None:
        return data if isinstance(data, (dict, list)) else {"result": data}
    structured = getattr(result, "structured_content", None) or getattr(
        result, "structuredContent", None
    )
    return structured or None


def _content_blocks(result: Any) -> list[Any]:
    blocks = getattr(result, "content", None)
    if blocks is None and isinstance(result, list):
        blocks = result
    return list(blocks or [])


def _binary_from_block(block: Any) -> BinaryContentResult | None:
    """Decode a block that carries bytes, or return None if it carries text."""
    btype = getattr(block, "type", None)
    if btype in ("image", "audio") or getattr(block, "data", None):
        raw = base64.b64decode(getattr(block, "data", "") or "")
        return BinaryContentResult.from_bytes(
            raw, media_type=getattr(block, "mimeType", None)
        )
    if btype == "resource":
        resource = getattr(block, "resource", None)
        blob = getattr(resource, "blob", None)
        if blob:
            return BinaryContentResult.from_bytes(
                base64.b64decode(blob), media_type=getattr(resource, "mimeType", None)
            )
    return None


def _block_text(block: Any) -> str | None:
    if getattr(block, "type", None) == "text":
        return getattr(block, "text", "") or None
    if getattr(block, "type", None) == "resource":
        resource = getattr(block, "resource", None)
        return getattr(resource, "text", None)
    return None


def _map_content_blocks(blocks: list[Any]) -> Any:
    """Collapse content blocks to JSON, or to the first binary block found."""
    texts: list[str] = []
    for block in blocks:
        binary = _binary_from_block(block)
        if binary is not None:
            return binary
        text = _block_text(block)
        if text:
            texts.append(text)

    joined = "\n".join(texts)
    if not joined:
        return {}
    try:
        parsed = json.loads(joined)
    except (ValueError, TypeError):
        return {"text": joined}
    return parsed if isinstance(parsed, (dict, list)) else {"result": parsed}
