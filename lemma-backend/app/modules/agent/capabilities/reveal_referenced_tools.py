"""Reveal the deferred tools a run is already told about.

Deferred tools (messaging, waiting, sub-agents, ...) are hidden behind
``search_tools`` until the model finds them. What a run finds is read back from
its history -- and a conversation's later run did not inherit it: the stored
``search_tools`` return comes back as a plain tool return, which pydantic-ai does
not read as a discovery. So a run woken with "read what they said with
``channel_read(asked_by_me=True)``", in a conversation whose history shows
``channel_read`` working, started with ``channel_read`` withheld. It never
searched -- why would it, it had the tool a turn ago -- and with no text answer
allowed it called the next tool in the list, 1,399 times.

So before the run's first request, a deferred tool is revealed when what the
model is about to read already names it:

* a ``search_tools`` return in history found it;
* history shows it being called;
* the instructions, the wake-up or a person's message names it as a call,
  ``name(``.

Only tools this run defines *and* defers: this can surface nothing the agent was
not granted, and leaves every visible tool alone. It reveals the way
pydantic-ai's own deferred capabilities do -- a ``ToolAvailabilityDeltaPart``
appended for the run, rendered per provider -- so stored history is never
rewritten and its cached prefix stays as it was.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolAvailabilityDeltaPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext

from app.core.log.log import get_logger

logger = get_logger(__name__)

_SEARCH_TOOLS = "search_tools"
#: A tool named the way instructions and wake-ups name one: as a call.
_CALL_SHAPED = re.compile(r"\b([a-z][a-z0-9_]*)\(")


def referenced_tool_names(
    messages: Iterable[ModelMessage], instructions: Iterable[str] = ()
) -> set[str]:
    """Every tool name the model is about to read as found, called or named."""
    names: set[str] = set()
    texts: list[str] = list(instructions)
    for message in messages:
        if isinstance(message, ModelResponse):
            names.update(
                part.tool_name
                for part in message.parts
                if isinstance(part, ToolCallPart)
            )
            continue
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and part.tool_name == _SEARCH_TOOLS:
                names.update(_found(part.content))
            elif isinstance(part, UserPromptPart | SystemPromptPart):
                texts.extend(_texts(part.content))
    for text in texts:
        names.update(_CALL_SHAPED.findall(text))
    return names


def _found(content: object) -> list[str]:
    """The names a stored ``search_tools`` return lists, whatever its shape now."""
    if not isinstance(content, Mapping):
        return []
    matches = content.get("discovered_tools")
    if not isinstance(matches, list):
        return []
    return [
        match["name"]
        for match in matches
        if isinstance(match, Mapping) and isinstance(match.get("name"), str)
    ]


def _texts(content: object) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, list | tuple):
        return [item for item in content if isinstance(item, str)]
    return []


class RevealReferencedToolsCapability(AbstractCapability[Any]):
    """Reveals, before a run's first request, the deferred tools it is told about."""

    async def before_model_request(
        self, ctx: RunContext[Any], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        # Once, on the first request: later ones carry the delta in their
        # history, and anything the run itself finds it finds by searching.
        if ctx.run_step > 1:
            return request_context
        parameters = request_context.model_request_parameters
        deferred = {
            tool.name for tool in parameters.function_tools if tool.defer_loading
        }
        if not deferred:
            return request_context
        instructions = [part.content for part in parameters.instruction_parts or ()]
        referenced = referenced_tool_names(request_context.messages, instructions)
        tools_added = sorted(
            (referenced & deferred)
            - ctx.discovered_tool_names
            - parameters.revealed_tool_names
        )
        if not tools_added:
            return request_context
        delta = ModelRequest(parts=[ToolAvailabilityDeltaPart(tools_added=tools_added)])
        request_context.messages = [*request_context.messages, delta]
        ctx.messages.append(delta)
        ctx.discovered_tool_names.update(tools_added)
        request_context.model_request_parameters = replace(
            parameters,
            revealed_tool_names=parameters.revealed_tool_names | set(tools_added),
        )
        logger.info(
            "agent.capabilities.referenced_tools.revealed",
            tools=tools_added,
        )
        return request_context


__all__ = ["RevealReferencedToolsCapability", "referenced_tool_names"]
