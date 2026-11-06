"""ToolSpecs as an MCP listing, and MCP tool calls run through the ToolRuntime.

Mounted twice: by the pod MCP server, over a pod's tools for a pod token, and
by the Agent Host's conversation MCP, over a run's tools relayed on the host
link. Both show the same specs the in-process harness shows its model, with
``lemma_``-prefixed names and schemas inlined for clients that cannot follow a
``$ref``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from mcp.types import CallToolResult, Tool, ToolAnnotations

from app.core.log.log import get_logger
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.tools import ToolAccess, ToolPolicy, ToolSpec
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.mcp import exported_tool_name
from app.modules.agent.infrastructure.mcp.content import (
    tool_call_error,
    tool_call_result,
)
from app.modules.agent.infrastructure.providers.control_flow import (
    is_control_flow_exception,
)
from app.modules.agent.infrastructure.providers.tool_schemas import (
    inline_tool_schema_refs,
)
from app.modules.agent.services.tool_runtime import ToolRuntime

logger = get_logger(__name__)

#: The ``_meta`` an MCP Apps view adds to a tool, by the view's name.
type ViewMeta = Callable[[str], JsonObject]


@dataclass(frozen=True, slots=True)
class McpCall:
    """What a call answered: the MCP result, and the payload it was made from."""

    result: CallToolResult
    payload: object


async def mcp_tools(
    runtime: ToolRuntime,
    ctx: ToolContext,
    *,
    meta: JsonObject | None = None,
    view_meta: ViewMeta | None = None,
) -> list[Tool]:
    """Every tool in ``runtime`` as an MCP client lists it.

    ``view_meta`` is given by a server that shows outside clients each tool's
    title, annotations and MCP Apps view; the Agent Host is shown none of them.
    """
    return [
        await _mcp_tool(spec, runtime, ctx, meta=meta, view_meta=view_meta)
        for spec in runtime.specs
    ]


async def call_mcp_tool(
    runtime: ToolRuntime,
    name: str,
    arguments: Mapping[str, object] | None,
    ctx: ToolContext,
    *,
    tool_call_id: str | None = None,
    shape: Callable[[object], object] | None = None,
) -> McpCall:
    """Run one call; a refusal comes back as an MCP tool error, never raised.

    ``shape`` adjusts a returned payload before it is rendered.
    """
    try:
        payload = await runtime.call(name, arguments, ctx, tool_call_id=tool_call_id)
    except Exception as exc:  # noqa: BLE001 - graceful tool-error boundary
        if is_control_flow_exception(exc):
            raise
        # An unknown name or arguments that do not validate: the client's
        # model reads it and recovers, instead of the run aborting on a
        # protocol error.
        logger.warning("agent.mcp.tool_call_refused.degraded", exc_info=True)
        error = tool_call_error(name, exc)
        return McpCall(result=error, payload=error.structured_content)
    if shape is not None:
        payload = shape(payload)
    return McpCall(result=tool_call_result(payload), payload=payload)


async def _mcp_tool(
    spec: ToolSpec,
    runtime: ToolRuntime,
    ctx: ToolContext,
    *,
    meta: JsonObject | None,
    view_meta: ViewMeta | None,
) -> Tool:
    tool_meta: JsonObject = {"lemma_tool_name": spec.name, **(meta or {})}
    if view_meta is not None and spec.policy.view is not None:
        # Named for every caller: this server is stateless, so a client's
        # `initialize` capabilities are gone by the time it lists tools, and
        # a host that cannot draw the view ignores the key.
        tool_meta.update(view_meta(spec.policy.view))
    return Tool(
        name=exported_tool_name(spec.name),
        title=spec.policy.title if view_meta is not None else None,
        description=await runtime.description(spec, ctx),
        input_schema=inline_tool_schema_refs(spec.json_schema),
        annotations=_annotations(spec.policy) if view_meta is not None else None,
        _meta=tool_meta,
    )


def _annotations(policy: ToolPolicy) -> ToolAnnotations:
    """What Claude and ChatGPT read to decide whether to ask before a call.

    The MCP defaults are the cautious ones -- not read-only, destructive,
    open-world -- so an unannotated read would be confirmed every time.
    """
    return ToolAnnotations(
        title=policy.title,
        read_only_hint=policy.access is ToolAccess.READ,
        destructive_hint=policy.destructive,
        idempotent_hint=policy.idempotent,
        open_world_hint=policy.open_world,
    )


__all__ = ["McpCall", "call_mcp_tool", "mcp_tools"]
