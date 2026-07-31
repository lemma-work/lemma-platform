"""A toolset wrapper that turns tool-execution failures into tool responses.

Wrapping a toolset in ``GracefulToolset`` means a raising tool body (e.g. a
``function_*`` tool whose backend call fails) no longer aborts the in-process
LEMMA run: the exception is caught and returned as a structured error result, so
the model sees what went wrong and can adapt. pydantic-ai treats a returned value
as a successful tool return, so this also does NOT consume a tool retry.

Control-flow exceptions (``ModelRetry``, approval/deferral, usage limits,
cancellation) are re-raised untouched so the framework still handles them. Argument
*validation* errors happen before ``call_tool`` and are handled by the agent's
``retries`` budget, not here.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import ToolsetTool, WrapperToolset

from app.core.log.log import get_logger
from app.modules.agent.tools.tool_errors import (
    format_tool_error,
    is_control_flow_exception,
)

logger = get_logger(__name__)


class GracefulToolset(WrapperToolset[Any]):
    """Delegate to the wrapped toolset, but never let a tool body crash the run."""

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[Any],
        tool: ToolsetTool[Any],
    ) -> Any:
        try:
            return await self.wrapped.call_tool(name, tool_args, ctx, tool)
        except Exception as exc:  # noqa: BLE001 - intentional catch-all boundary
            if is_control_flow_exception(exc):
                raise
            logger.debug(
                'agent.graceful_toolset.tool_r_returning_model_instead.diagnostic',
                exc_info=True,
            )
            return format_tool_error(name, exc)
