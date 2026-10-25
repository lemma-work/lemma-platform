"""Keeping a whole request -- instructions, tools, history and reply -- in one window.

The compaction trigger and the hard ceiling used to measure the message history
alone. But the history is not the whole request: the system instructions (the
prompt, the brief, an attached document, the task list) and the tool schemas sit
in the same window, and together they are routinely a large share of it. The
reply budget (`max_tokens`) was meanwhile set to whatever the ceiling left over.
So a history held exactly at the ceiling, plus the instructions and tools, plus
the reply budget, overran the window by construction -- the provider rejection
the ceiling exists to prevent, arriving at exactly the point it was meant to
hold.

Both the instructions and the tool definitions are on the request context by
the time the history processors run, so they are measured there, per request,
and taken off the trigger and the ceiling. After the processors have done their
work, the reply budget is fitted to what is actually left.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelRequest
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition

from app.modules.agent.infrastructure.pydantic_ai_compat import ModelRequestContext
from app.modules.agent.infrastructure.providers.tokens import (
    MessageTokenCounter,
    count_text_tokens,
    count_tool_definition_tokens,
)
from app.modules.agent.services.run_input_settings import MIN_REPLY_TOKENS

#: Distinct tool definitions or instruction blocks one run remembers counts for.
#: A run's set changes only when a search reveals more tools.
_MAX_MEMO_ENTRIES = 512


@dataclass(slots=True)
class ContextFootprint:
    """What one run's requests carry besides the message history.

    Shared between the capability that measures it and the processors that
    budget the history against it, for the life of one run.
    """

    counter: MessageTokenCounter = field(default_factory=MessageTokenCounter)
    #: Instructions plus visible tool definitions, as of the latest request.
    fixed_tokens: int = 0
    _memo: dict[int, int] = field(default_factory=dict)

    def within(self, limit: int) -> int:
        """`limit` with the fixed part taken off it.

        Never below 1: a limit of 0 means "no limit" to everything that reads
        one, and a fixed part larger than the limit is a request that cannot
        fit, not one that should stop being bounded.
        """
        if limit <= 0:
            return limit
        return max(1, limit - self.fixed_tokens)

    def measure(self, instructions: str | None, tools: Sequence[ToolDefinition]) -> int:
        self.fixed_tokens = self._remembered(
            ("instructions", instructions or ""),
            lambda: count_text_tokens(instructions or ""),
        ) + sum(self._tool_tokens(tool) for tool in tools)
        return self.fixed_tokens

    def _tool_tokens(self, tool: ToolDefinition) -> int:
        schema = json.dumps(tool.parameters_json_schema, sort_keys=True, default=str)
        return self._remembered(
            ("tool", tool.name, tool.description or "", schema),
            lambda: count_tool_definition_tokens(
                tool.name, tool.description, tool.parameters_json_schema
            ),
        )

    def _remembered(self, key: tuple[str, ...], count: Callable[[], int]) -> int:
        digest = hash(key)
        cached = self._memo.get(digest)
        if cached is not None:
            return cached
        if len(self._memo) >= _MAX_MEMO_ENTRIES:
            self._memo.clear()
        self._memo[digest] = tokens = count()
        return tokens


def _instructions_text(request_context: ModelRequestContext) -> str | None:
    parts = request_context.model_request_parameters.instruction_parts
    if parts:
        return "\n\n".join(part.content for part in parts)
    for message in reversed(request_context.messages):
        if isinstance(message, ModelRequest) and message.instructions is not None:
            return message.instructions
    return None


def _visible_tools(request_context: ModelRequestContext) -> list[ToolDefinition]:
    """The tool definitions this request will actually carry.

    A deferred tool is not sent until a search reveals it, so counting every
    one would charge the window for schemas the model never sees.
    """
    parameters = request_context.model_request_parameters
    revealed = parameters.revealed_tool_names
    return [
        tool
        for tool in parameters.function_tools
        if not tool.defer_loading or tool.name in revealed
    ] + list(parameters.output_tools)


class MeasureRequestFootprint(AbstractCapability[object]):
    """Measure this request's instructions and tools before the history is fitted."""

    def __init__(self, footprint: ContextFootprint) -> None:
        self._footprint = footprint

    async def before_model_request(
        self,
        ctx: RunContext[object],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        self._footprint.measure(
            _instructions_text(request_context), _visible_tools(request_context)
        )
        return request_context


class FitReplyToWindow(AbstractCapability[object]):
    """Ask for no more reply than the window has left after everything else.

    Only ever lowers the cap a request already carries, never raises it or
    invents one: a model's own output limit can be smaller than its window, and
    a cap above that limit is a rejection of its own.
    """

    def __init__(self, footprint: ContextFootprint, *, window: int) -> None:
        self._footprint = footprint
        self._window = window

    async def before_model_request(
        self,
        ctx: RunContext[object],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        settings: ModelSettings = {**(request_context.model_settings or {})}
        current = settings.get("max_tokens")
        if self._window <= 0 or not isinstance(current, int) or current <= 0:
            return request_context
        used = self._footprint.fixed_tokens + self._footprint.counter.count(
            request_context.messages
        )
        # A floor even when the room is gone: a request that cannot fit is
        # rejected either way, and a cap too small for one tool call would
        # turn every request that *does* fit into a guaranteed truncation.
        fitted = min(current, max(MIN_REPLY_TOKENS, self._window - used))
        if fitted != current:
            settings["max_tokens"] = fitted
            request_context.model_settings = settings
        return request_context
