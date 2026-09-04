"""MCP connector executor — proxy operations to an external MCP server's tools.

An ``mcp``-kind connector stores the MCP server URL on the auth-config
(``connection_config.server_url``) and the bearer token on the account
(``third_party_credentials``). Each operation's ``execution`` descriptor names the
tool (``tool_name``); executing calls ``tools/call`` on the server and maps the
result back to JSON (or a ``BinaryContentResult`` for binary content blocks).

An install may also carry ``connection_config.session_setup``: calls replayed at
the start of every session, because a session here lasts exactly one tool call
and some servers gate their tools behind session-scoped state. See
``MCP_SESSION_SETUP_KEY``.

The MCP client factory is injectable so tests can drive an in-memory server.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any, Callable, Protocol

import httpx
from fastmcp.exceptions import FastMCPError, McpError, ToolError

from lemma_connectors.core.results import BinaryContentResult

from app.core.log.log import get_logger
from app.core.net.url_guard import UnsafeUrlError, assert_safe_url
from app.core.redaction import redact_text
from app.modules.connectors.domain.errors import (
    OperationExecutionInfrastructureError,
    OperationExecutionValidationError,
)

logger = get_logger(__name__)

# Matches `plumbing._UPSTREAM_MESSAGE_LIMIT`: enough of the provider's own words
# to debug with, not enough to be a payload dump.
_UPSTREAM_MESSAGE_LIMIT = 2000

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

#: Install-config key holding the calls to replay at the start of every session.
#:
#: A session is exactly one tool call long here -- the client is built, used and
#: closed per operation -- so any server-side state a tool sets up is gone before
#: the next call sees it. Servers that gate their tools behind a session-scoped
#: switch (Arize Phoenix's ``enable_tool_group`` is the one that prompted this)
#: are therefore unusable: the gated tools are not in ``list_tools()`` on a
#: virgin session, so they are never discovered, never stored as operations, and
#: never addressable by name.
#:
#: Replaying the setup on every session is what makes them reachable without
#: keeping a session alive across calls -- which would need replica affinity and
#: eviction, and would put session state where nothing else in this module keeps
#: any. Discovery replays it too, so the tools it unlocks are discovered rather
#: than merely callable.
MCP_SESSION_SETUP_KEY = "session_setup"


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


class McpToolSession(Protocol):
    """The part of a connected MCP client this module calls.

    Stated rather than left as `Any`, because the setup replay is handed a
    client by two different callers and what it is allowed to do with one is
    exactly this.
    """

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
        *,
        raise_on_error: bool,
    ) -> object:
        """Call one tool and return its result without raising on a refusal.

        `raise_on_error=False` is not optional here. fastmcp defaults it to
        True, which turns a tool's refusal into a `ToolError` that reads as the
        transport failing -- the whole defect this protocol's callers exist to
        avoid. Stating it in the signature is what stops it being forgotten.
        """


def _session_setup_steps(
    connection_config: Mapping[str, object] | None,
) -> list[tuple[str, dict[str, object]]]:
    """The install's setup calls, as ``(tool_name, arguments)`` pairs.

    Read defensively rather than trusted: this is tenant-written JSON, and an
    entry that is not a usable call is skipped instead of failing every
    operation on the install.
    """
    raw = (connection_config or {}).get(MCP_SESSION_SETUP_KEY)
    if not isinstance(raw, list):
        return []
    steps: list[tuple[str, dict[str, object]]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        tool_name = entry.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name:
            continue
        arguments = entry.get("arguments")
        steps.append((tool_name, arguments if isinstance(arguments, dict) else {}))
    return steps


async def apply_session_setup(
    client: McpToolSession, connection_config: Mapping[str, object] | None
) -> None:
    """Replay the install's setup calls on a freshly opened session.

    Called by both the executor and the discoverer, immediately after the
    session opens and before anything else is asked of it. A step that the
    server rejects fails the whole call, and says which step it was -- a
    half-applied preamble is a session whose tool list is neither the configured
    one nor the default, and silently continuing would make that look like the
    operation itself misbehaving.
    """
    for tool_name, arguments in _session_setup_steps(connection_config):
        result = await client.call_tool(tool_name, arguments, raise_on_error=False)
        raise_on_tool_error(tool_name, result, during_setup=True)


def raise_on_tool_error(
    tool_name: str, result: object, *, during_setup: bool = False
) -> None:
    """Report a tool that answered ``isError`` as the server rejecting the call.

    Not an outage, and two things followed from calling it one. The breaker
    counts infrastructure errors, so five bad-argument calls inside the window
    disabled a healthy MCP server for the whole organization. And
    ``OperationExecutionInfrastructureError`` hardcodes its own message, so the
    tool's explanation was discarded and the caller got "Connector provider is
    temporarily unavailable" with no way to correct itself and every reason to
    retry. Transport failures are still classified separately, where they
    belong.
    """
    if not getattr(result, "is_error", False):
        return
    text = _collect_text(getattr(result, "content", None) or [])
    raise OperationExecutionValidationError(
        "",
        details={
            "provider": "mcp",
            "reason": "session_setup_failed" if during_setup else "tool_error",
            # Named `operation_name` because that is the key the error's own
            # detail allowlist lets through; for a setup step it is the step's
            # tool, which is the whole point of saying it.
            "operation_name": tool_name,
            "upstream_message": redact_text(text)[:_UPSTREAM_MESSAGE_LIMIT],
        },
    )


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
        # Re-check the target at execution, not only at install: the stored
        # server_url is tenant-supplied, and a DNS rebind (or any change since
        # install) could aim it at the metadata service or an internal host.
        # The HTTP executor does the same for exactly this reason.
        try:
            await assert_safe_url(str(server_url))
        except UnsafeUrlError as exc:
            raise OperationExecutionValidationError(
                f"Refusing to call an unsafe MCP target: {exc}",
                details={"reason": exc.reason},
            ) from exc
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
                await apply_session_setup(client, connection_config)
                # `raise_on_error=False` is load-bearing. fastmcp defaults it to
                # True, so a tool answering `isError` raised `ToolError` -- a
                # `FastMCPError`, which `_is_transport_failure` accepts -- before
                # `_map_result` could ever see the result. Every tool-level
                # refusal was therefore reported as a provider outage, and the
                # classification below was unreachable code that a test calling
                # `_map_result` directly certified as working.
                result = await client.call_tool(
                    tool_name, payload or {}, raise_on_error=False
                )
        except OperationExecutionValidationError, OperationExecutionInfrastructureError:
            raise
        except ToolError as exc:
            # Belt and braces: `raise_on_error=False` covers the direct path,
            # but a transport that wraps a tool error still must not be read as
            # the server being down.
            message = _flatten_message(exc)
            raise OperationExecutionValidationError(
                "",
                details={
                    "provider": "mcp",
                    "reason": "tool_error",
                    "operation_name": tool_name,
                    "upstream_message": redact_text(message)[:_UPSTREAM_MESSAGE_LIMIT],
                },
            ) from exc
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
        # A tool that answers `is_error` is the server rejecting this call -- a
        # bad argument, a missing project, a refusal it chose.
        raise_on_tool_error(tool_name, result)
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
    except ValueError, TypeError:
        return {"text": joined}
    return parsed if isinstance(parsed, (dict, list)) else {"result": parsed}
