"""No tool starts on a worker that may no longer hold its turn.

A turn's steps are fenced by its lease, but a tool's effects are not: a pod
record, a Slack message or a shell command lands wherever the call ran. A
worker whose heartbeat cannot reach the database keeps running the turn until
the heartbeat gives up on it, and by then another worker may have resumed the
same turn. So each tool call first asks the lease (``JobContext.lease_alive``,
the local clock against the last extension that committed) and, when it is not
alive, ends the turn there with ``LeaseLost`` -- the run is left RUNNING for
whoever holds it now, and nothing is charged.

``wrap_tool_execute`` is the one hook every in-process tool call passes
through, whatever toolset or capability brought the tool. A call already
running is not interrupted here; the heartbeat cancels it.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic_ai import RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    ValidatedToolArgs,
    WrapToolExecuteHandler,
)
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from app.core.infrastructure.work import LeaseLost
from app.core.log.log import get_logger

logger = get_logger(__name__)


class LeaseGateCapability(AbstractCapability[object]):
    """Stop the turn before a tool call once its lease may have lapsed."""

    def __init__(self, lease_alive: Callable[[], bool]) -> None:
        self._lease_alive = lease_alive

    def get_serialization_name(self) -> str | None:  # pragma: no cover - metadata
        return "lease_gate"

    async def wrap_tool_execute(
        self,
        ctx: RunContext[object],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> object:
        if not self._lease_alive():
            logger.warning(
                "agent.turn.tool_skipped_lease_lost",
                tool_name=tool_def.name,
                tool_call_id=call.tool_call_id,
            )
            raise LeaseLost(f"tool {tool_def.name} not started: lease may be lost")
        return await handler(args)
