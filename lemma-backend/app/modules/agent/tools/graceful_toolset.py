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

import asyncio
import re
import time

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import ToolsetTool, WrapperToolset

from app.core.log.log import get_logger
from app.modules.agent.services.run_phase_spans import run_phase
from app.modules.agent.tools.tool_errors import (
    format_tool_error,
    is_control_flow_exception,
)

logger = get_logger(__name__)

_SPAN_NAME_SAFE = re.compile(r"[^a-z0-9_]+")


def _tool_span_name(name: str) -> str:
    """Span suffix for a tool, in the shape the span sanitizer preserves.

    Only lowercase dotted/underscored names survive export with their own name,
    so a tool called ``Fetch-Report`` has to arrive as ``fetch_report``. Without
    this the whole per-tool breakdown collapses into one generic span.
    """
    safe = _SPAN_NAME_SAFE.sub("_", name.lower()).strip("_")
    return f"tool.{safe or 'unnamed'}"


class GracefulToolset[DepsT](WrapperToolset[DepsT]):
    """Delegate to the wrapped toolset, but never let a tool body crash the run."""

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, object],
        ctx: RunContext[DepsT],
        tool: ToolsetTool[DepsT],
    ) -> object:
        started = time.monotonic()
        try:
            with run_phase(_tool_span_name(name)):
                return await self.wrapped.call_tool(name, tool_args, ctx, tool)
        except asyncio.CancelledError:
            # Named here because this is the only place that knows *which* tool
            # was in flight. `reraise_driver_failure` already separates who
            # asked for the cancellation -- its two counters say whether it came
            # from outside or was aimed at the driver alone -- but the traceback
            # it logs shows where the task was suspended, which is some poll
            # loop three layers down, and the frames it captures are all
            # harness. An incident spent on that ended in inference, and this
            # line is the half that was missing from the answer.
            #
            # Re-raised bare: cancellation is the framework's to handle, and
            # swallowing it here is how a truncated run reports success.
            logger.warning(
                "agent.graceful_toolset.tool_cancelled_mid_flight.degraded",
                tool_name=name,
                elapsed_seconds=round(time.monotonic() - started, 3),
            )
            raise
        except Exception as exc:  # noqa: BLE001 - intentional catch-all boundary
            if is_control_flow_exception(exc):
                raise
            logger.warning(
                "agent.graceful_toolset.tool_r_returning_model_instead.degraded",
                exc_info=True,
            )
            return format_tool_error(name, exc)
