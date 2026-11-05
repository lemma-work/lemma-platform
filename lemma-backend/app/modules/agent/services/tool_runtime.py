"""One tool runtime: every tool call, on every surface, runs here.

The in-process harness (through ``harness/lemma/toolset_adapter``), both MCP
mounts (``mcp/toolspec_adapter``) and the approval executor hand a call to a
``ToolRuntime`` built over the run's specs. It looks the spec up, validates the
arguments, keeps a run answering somebody outside the pod to the tools its
policy allows, and turns a failure into a result the model can read -- counting
and tracing each call as it ends, including the ones that end by returning a
failure.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Iterable, Mapping

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from app.core.log.log import get_logger
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.outsiders import run_audience
from app.modules.agent.domain.tools import ToolSpec
from app.modules.agent.infrastructure.providers.control_flow import (
    is_control_flow_exception,
)
from app.modules.agent.infrastructure.tools.tool_errors import (
    format_tool_error,
    result_is_failure,
)
from app.modules.agent.services.run_metrics import AgentRunMetrics, agent_run_metrics
from app.modules.agent.services.run_phase_spans import run_phase

logger = get_logger(__name__)

WITHHELD_MESSAGE = "That tool is not available when answering someone outside the pod."

_SPAN_NAME_SAFE = re.compile(r"[^a-z0-9_]+")
# Longest error text put on a span; the exporter truncates at 256 anyway.
_MAX_SPAN_ERROR_CHARS = 200


class UnknownToolError(ValueError):
    """Raised when a tool name is not one of this run's tools."""


class ToolRuntime:
    """A run's tools, by name, and the one way to call them."""

    def __init__(
        self,
        specs: Iterable[ToolSpec],
        *,
        metrics: AgentRunMetrics = agent_run_metrics,
    ) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            if spec.name in self._specs:
                raise ValueError(f"Duplicate tool name: {spec.name}")
            self._specs[spec.name] = spec
        self._metrics = metrics

    @property
    def specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    async def description(self, spec: ToolSpec, ctx: ToolContext) -> str:
        """What this run is told the tool does."""
        if spec.describe is None:
            return spec.description
        return await spec.describe(ctx, spec.description)

    async def call(
        self,
        name: str,
        arguments: Mapping[str, object] | None,
        ctx: ToolContext,
        *,
        tool_call_id: str | None = None,
    ) -> object:
        """Run ``name`` with raw arguments, as an MCP client or a re-run sends them.

        An unknown name or arguments that do not validate raise; anything the
        tool body raises comes back as a result (``run``).
        """
        spec = self._specs.get(name)
        if spec is None:
            raise UnknownToolError(name)
        return await self.run(
            spec, spec.parse(arguments or {}), ctx, tool_call_id=tool_call_id
        )

    async def run(
        self,
        spec: ToolSpec,
        args: object,
        ctx: ToolContext,
        *,
        tool_call_id: str | None = None,
    ) -> object:
        """Run one validated call; a raising tool body never ends the run."""
        if run_audience(ctx).answers_outsiders and not spec.policy.outsider_allowed:
            logger.warning(
                "agent.outsider_tool_gate.withheld.degraded", tool_name=spec.name
            )
            return {"success": False, "error": WITHHELD_MESSAGE}
        call_ctx = ctx.model_copy(update={"tool_call_id": tool_call_id, "tools": self})
        started = time.monotonic()
        # Captured before ours becomes current: on the in-process path this is
        # the span pydantic-ai opened for the call, which is the one the LLM
        # backend receives.
        caller_span = trace.get_current_span()
        kind = spec.policy.kind
        with run_phase(_tool_span_name(spec.name)) as span:
            try:
                result = await spec.run(call_ctx, args)
            except asyncio.CancelledError:
                # Named here because this is the only place that knows which
                # tool was in flight; the traceback a cancellation logs shows
                # some poll loop three layers down. Re-raised bare: swallowing
                # it is how a truncated run reports success.
                span.set_attribute("lemma.outcome", "cancelled")
                self._metrics.tool_call(tool_kind=kind, outcome="cancelled")
                logger.warning(
                    "agent.graceful_toolset.tool_cancelled_mid_flight.degraded",
                    tool_name=spec.name,
                    elapsed_seconds=round(time.monotonic() - started, 3),
                )
                raise
            except Exception as exc:  # noqa: BLE001 - intentional catch-all boundary
                if is_control_flow_exception(exc):
                    # A pause, a retry or a usage limit is a signal, not a
                    # failure. OK is final in the SDK, so setting it before the
                    # span context managers stamp ERROR on the way out keeps
                    # every pause off the error rate, on both pipelines.
                    span.set_attribute("lemma.outcome", "control_flow")
                    self._metrics.tool_call(tool_kind=kind, outcome="control_flow")
                    span.set_status(Status(StatusCode.OK))
                    caller_span.set_status(Status(StatusCode.OK))
                    raise
                logger.warning(
                    "agent.graceful_toolset.tool_r_returning_model_instead.degraded",
                    exc_info=True,
                )
                payload = format_tool_error(spec.name, exc)
                _mark_failure(span, payload)
                self._metrics.tool_call(tool_kind=kind, outcome="error")
                return payload
            if result_is_failure(result):
                _mark_failure(span, result)
                self._metrics.tool_call(tool_kind=kind, outcome="error")
            else:
                span.set_attribute("lemma.outcome", "ok")
                self._metrics.tool_call(tool_kind=kind, outcome="ok")
            return result


def _tool_span_name(name: str) -> str:
    """Span suffix for a tool, in the shape the span sanitizer preserves.

    Only lowercase dotted/underscored names survive export with their own name,
    so ``Fetch-Report`` has to arrive as ``fetch_report``.
    """
    safe = _SPAN_NAME_SAFE.sub("_", name.lower()).strip("_")
    return f"tool.{safe or 'unnamed'}"


def _mark_failure(span: Span, payload: object) -> None:
    """Mark a span for a tool that reported a failure, raised or returned.

    Without this a returned failure leaves the span UNSET, indistinguishable
    from a success. Only keys the exporter already allows are set.
    """
    span.set_attribute("lemma.outcome", "error")
    description = ""
    if isinstance(payload, dict):
        error_type = payload.get("error_type")
        if error_type:
            span.set_attribute("error.type", str(error_type))
        code = payload.get("code")
        if code:
            span.set_attribute("error.code", str(code))
        description = str(payload.get("error") or "")[:_MAX_SPAN_ERROR_CHARS]
    span.set_status(Status(StatusCode.ERROR, description or None))


__all__ = ["ToolRuntime", "UnknownToolError", "WITHHELD_MESSAGE"]
