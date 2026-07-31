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

from lemma_connectors.core.results import BinaryContentResult

from app.core.log.log import get_logger
from app.modules.connectors.domain.errors import (
    OperationExecutionInfrastructureError,
    OperationExecutionValidationError,
)

logger = get_logger(__name__)

# (server_url, headers) -> an async-context-manager MCP client exposing
# ``list_tools()`` and ``call_tool(name, args)``.
McpClientFactory = Callable[[str, dict[str, str]], Any]


def default_mcp_client_factory(server_url: str, headers: dict[str, str]) -> Any:
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    return Client(StreamableHttpTransport(url=server_url, headers=headers))


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
    ) -> Any:
        server_url = (connection_config or {}).get("server_url")
        if not server_url:
            raise OperationExecutionValidationError("MCP connection requires 'server_url'.")
        tool_name = (execution or {}).get("tool_name") or operation_name
        headers = build_mcp_headers(connection_config, third_party_credentials)

        logger.debug(
            "connectors.mcp_executor.calling_mcp_tool.observed",
            connector_id=connector_id,
            tool_name=tool_name,
        )
        try:
            client = self._client_factory(server_url, headers)
            async with client:
                result = await client.call_tool(tool_name, payload or {})
        except (OperationExecutionValidationError, OperationExecutionInfrastructureError):
            raise
        except Exception as exc:  # noqa: BLE001 - map any client/transport error cleanly
            raise OperationExecutionInfrastructureError(
                f"MCP tool '{tool_name}' failed: {exc}",
                details={"provider": "mcp", "upstream_message": str(exc)},
            ) from exc

        return self._map_result(tool_name, result)

    def _map_result(self, tool_name: str, result: Any) -> Any:
        if getattr(result, "is_error", False):
            text = _collect_text(getattr(result, "content", None) or [])
            raise OperationExecutionInfrastructureError(
                f"MCP tool '{tool_name}' returned an error: {text}",
                details={"provider": "mcp"},
            )
        # Prefer structured output when the server provides it.
        data = getattr(result, "data", None)
        if data is not None:
            return data if isinstance(data, (dict, list)) else {"result": data}
        structured = getattr(result, "structured_content", None) or getattr(
            result, "structuredContent", None
        )
        if structured:
            return structured

        blocks = getattr(result, "content", None)
        if blocks is None and isinstance(result, list):
            blocks = result
        blocks = blocks or []

        texts: list[str] = []
        for block in blocks:
            btype = getattr(block, "type", None)
            if btype == "text":
                texts.append(getattr(block, "text", "") or "")
            elif btype in ("image", "audio") or getattr(block, "data", None):
                raw = base64.b64decode(getattr(block, "data", "") or "")
                return BinaryContentResult.from_bytes(
                    raw, media_type=getattr(block, "mimeType", None)
                )
            elif btype == "resource":
                resource = getattr(block, "resource", None)
                blob = getattr(resource, "blob", None)
                if blob:
                    return BinaryContentResult.from_bytes(
                        base64.b64decode(blob), media_type=getattr(resource, "mimeType", None)
                    )
                res_text = getattr(resource, "text", None)
                if res_text:
                    texts.append(res_text)

        joined = "\n".join(t for t in texts if t)
        if not joined:
            return {}
        try:
            parsed = json.loads(joined)
            return parsed if isinstance(parsed, (dict, list)) else {"result": parsed}
        except (ValueError, TypeError):
            return {"text": joined}


def _collect_text(blocks: Any) -> str:
    parts = []
    for block in blocks or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return " ".join(parts)
