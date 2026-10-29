"""Deferred tools reached through two fixed entries: ``search_tools`` and ``call_tool``.

The pod's own assistant keeps its optional toolsets out of the prompt and finds
them on demand. Where the provider has a channel for tools added mid-run
(Anthropic's ``tool_reference``, OpenAI Responses' ``additional_tools``), the
harness uses pydantic-ai's own tool search and the request's ``tools`` stay as
they were. Plain Chat Completions has no such channel: a revealed tool joins the
``tools`` array, which chat templates render ahead of every message, so a reveal
re-bills the whole conversation -- and the next run, which replays the reveal,
starts from the same changed array.

There, the array never changes: ``search_tools`` reads out a deferred tool's
description and parameters, and ``call_tool`` runs it by name. The call is
logged as the model made it (``call_tool``); everything a person or a surface
reads -- the stored call, its return, its pause -- names the tool it ran
(``as_called``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

from pydantic import BaseModel, Field

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.tools import (
    ReplayClass,
    ToolAccess,
    ToolGroup,
    ToolPolicy,
    ToolSpec,
    tool,
)

#: The name the model calls a deferred tool through.
CALL_TOOL = "call_tool"
#: The name it searches the deferred tools with.
SEARCH_TOOLS = "search_tools"
#: At most this many tools per search: enough to choose between, few enough
#: that the result stays a small part of the history it joins.
_MAX_MATCHES = 8


class SearchTools(BaseModel):
    queries: list[str] = Field(
        description=(
            "What you need a tool to do, in a few words each, or tool names. "
            "Each query is matched on its own."
        ),
        min_length=1,
    )


class CallTool(BaseModel):
    name: str = Field(description="The tool's name, as search_tools returned it.")
    arguments: dict[str, object] = Field(
        default_factory=dict,
        description="The tool's arguments, matching the parameters search_tools returned.",
    )


def deferred_entry_specs(deferred: Sequence[ToolSpec]) -> list[ToolSpec]:
    """The two fixed entries over ``deferred``, the tools kept out of the prompt."""
    by_name = {spec.name: spec for spec in deferred}

    async def search_tools(ctx: ToolContext, request: SearchTools) -> object:
        """Find tools that are available on demand, with their parameters.

        Returns each matching tool's name, description and JSON-schema
        parameters. Run one with `call_tool`.
        """
        matches = _matches(by_name.values(), request.queries)
        return {
            "tools": [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.json_schema,
                }
                for spec in matches
            ],
            "note": (
                "Run one with call_tool(name, arguments)."
                if matches
                else "Nothing matched. Available: " + ", ".join(sorted(by_name))
            ),
        }

    async def call_tool(ctx: ToolContext, request: CallTool) -> object:
        """Run a tool that is available on demand, by its name and arguments.

        Use search_tools first to read the tool's parameters.
        """
        if request.name not in by_name or ctx.tools is None:
            return {
                "success": False,
                "error": (
                    f"No on-demand tool is named {request.name!r}. "
                    "Use search_tools to find one."
                ),
            }
        try:
            return await ctx.tools.call(
                request.name, request.arguments, ctx, tool_call_id=ctx.tool_call_id
            )
        except ValueError as error:
            # Arguments that do not validate (pydantic's ValidationError is a
            # ValueError): said back, for the model to correct.
            return {"success": False, "error": f"Invalid arguments: {error}"}

    return [
        tool(
            search_tools,
            ToolGroup.DEFERRED,
            ToolPolicy(access=ToolAccess.READ, replay=ReplayClass.SAFE),
        ),
        tool(call_tool, ToolGroup.DEFERRED),
    ]


def as_called(tool_name: str | None, args: object) -> tuple[str, object] | None:
    """The tool a ``call_tool`` call ran and its arguments; ``None`` otherwise."""
    if tool_name != CALL_TOOL or not isinstance(args, Mapping):
        return None
    name, arguments = args.get("name"), args.get("arguments", {})
    if not isinstance(name, str) or not isinstance(arguments, Mapping):
        return None
    return name, dict(arguments)


def _matches(specs: Iterable[ToolSpec], queries: Sequence[str]) -> list[ToolSpec]:
    """The specs any query names or describes, best first, name-sorted on ties."""
    pool = sorted(specs, key=lambda spec: spec.name)
    scored: dict[str, tuple[int, ToolSpec]] = {}
    for query in queries:
        words = [word for word in re.split(r"\W+", query.lower()) if len(word) > 1]
        for spec in pool:
            text = f"{spec.name} {spec.description}".lower()
            score = (10 if query.strip().lower() == spec.name else 0) + sum(
                word in text for word in words
            )
            if score and score > scored.get(spec.name, (0, spec))[0]:
                scored[spec.name] = (score, spec)
    ranked = sorted(scored.values(), key=lambda item: (-item[0], item[1].name))
    return [spec for _, spec in ranked[:_MAX_MATCHES]]


__all__ = [
    "CALL_TOOL",
    "SEARCH_TOOLS",
    "as_called",
    "deferred_entry_specs",
]
