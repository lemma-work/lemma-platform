"""The last word on which tools a run answering somebody outside the pod has.

Toolset selection and the assemblers each drop what they know to be unsafe for
a stranger's run. This sits after all of them, on the final tool list the model
is offered, and keeps only the names in ``tools/outsider_tools`` -- so a tool
that reached the run some way nobody thought of is still not callable.

Two hooks, because either alone leaves a door: ``prepare_tools`` takes the tool
out of the request (pydantic-ai's prepared toolset then refuses a call to it as
unknown), and ``wrap_tool_execute`` refuses to run one anyway, should a call
arrive by some path that did not go through the prepared list.
"""

from __future__ import annotations

from pydantic_ai import RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    ValidatedToolArgs,
    WrapToolExecuteHandler,
)
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from app.core.log.log import get_logger
from app.modules.agent.tools.outsider_tools import outsider_may_call

logger = get_logger(__name__)

#: What the model reads when it calls a tool this run does not have.
WITHHELD_MESSAGE = "That tool is not available when answering someone outside the pod."


class OutsiderToolGateCapability(AbstractCapability[object]):
    """Keep only the allow-listed tools on a stranger's run."""

    def get_serialization_name(self) -> str | None:  # pragma: no cover - metadata
        return "outsider_tool_gate"

    async def prepare_tools(
        self,
        ctx: RunContext[object],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        kept = [tool for tool in tool_defs if outsider_may_call(tool.name)]
        for tool in tool_defs:
            if not outsider_may_call(tool.name):
                logger.warning(
                    "agent.outsider_tool_gate.withheld.degraded",
                    tool_name=tool.name,
                )
        return kept

    async def wrap_tool_execute(
        self,
        ctx: RunContext[object],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> object:
        if not outsider_may_call(tool_def.name):
            logger.warning(
                "agent.outsider_tool_gate.withheld.degraded",
                tool_name=tool_def.name,
            )
            return {"success": False, "error": WITHHELD_MESSAGE}
        return await handler(args)
