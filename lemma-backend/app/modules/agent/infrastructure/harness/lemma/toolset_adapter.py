"""ToolSpecs as pydantic-ai toolsets, every call routed through the ToolRuntime.

pydantic-ai validates a call's arguments itself -- from a function signature
built for the spec, so a model's malformed call is retried exactly as before --
and hands the validated request to ``ToolRuntime.run``. The JSON schema and the
description the model is shown are the spec's own, the same ones the MCP
listing serves. Pausing tools keep raising ``AgentInputRequired``.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

from pydantic import TypeAdapter
from pydantic_ai import BinaryContent, ToolReturn
from pydantic_ai.tools import GenerateToolJsonSchema, RunContext, Tool, ToolDefinition
from pydantic_ai.toolsets import FunctionToolset
from pydantic_core import SchemaValidator

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.tools import ToolGroup, ToolSpec, WithImages
from app.modules.agent.infrastructure.providers.pydantic_ai_compat import (
    FunctionSchema,
    function_schema,
)
from app.modules.agent.services.tool_runtime import ToolRuntime

#: The ids three per-run toolsets have always carried on their definitions.
_TOOLSET_IDS = {
    ToolGroup.TODO: "lemma_todo",
    ToolGroup.CONTACT: "contact",
    ToolGroup.FORM: "visitor_form",
}


def function_toolset(
    specs: Sequence[ToolSpec], runtime: ToolRuntime
) -> FunctionToolset[ToolContext]:
    """One toolset over ``specs``, which are expected to share a group."""
    groups = {spec.toolset for spec in specs}
    toolset_id = _TOOLSET_IDS.get(next(iter(groups))) if len(groups) == 1 else None
    return FunctionToolset[ToolContext](
        tools=[_tool(spec, runtime) for spec in specs], id=toolset_id
    )


def as_tool_return(result: object) -> object:
    """A runtime result as pydantic-ai returns it: images as binary content."""
    if not isinstance(result, WithImages):
        return result
    return ToolReturn(
        return_value=result.value,
        content=[
            BinaryContent(data=image.data, media_type=image.media_type)
            for image in result.images
        ],
    )


def _tool(spec: ToolSpec, runtime: ToolRuntime) -> Tool[ToolContext]:
    async def call(ctx: RunContext[ToolContext], **arguments: object) -> object:
        args = arguments[spec.argument] if spec.parameters is not None else arguments
        result = await runtime.run(spec, args, ctx.deps, tool_call_id=ctx.tool_call_id)
        return as_tool_return(result)

    call.__name__ = spec.name
    return Tool(
        call,
        name=spec.name,
        description=spec.description,
        takes_ctx=True,
        strict=spec.strict,
        prepare=_describing(spec, runtime) if spec.describe is not None else None,
        function_schema=_function_schema(spec, call),
    )


def _function_schema(
    spec: ToolSpec, call: Callable[..., Awaitable[object]]
) -> FunctionSchema:
    """How pydantic-ai validates and calls the tool.

    A spec with a request model gets the validator pydantic-ai derives for a
    function taking that model, accepting the fields flat or wrapped under
    ``spec.argument``. One described only by a JSON schema takes the object as
    it comes.
    """
    if spec.parameters is None:
        return FunctionSchema(
            name=spec.name,
            function=call,
            description=spec.description,
            validator=cast(SchemaValidator, TypeAdapter(dict[str, Any]).validator),
            json_schema=spec.json_schema,
            takes_ctx=True,
            is_async=True,
        )

    async def signature(**_kwargs: object) -> None: ...

    annotations = {"ctx": RunContext[ToolContext], spec.argument: spec.parameters}
    signature.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [
            inspect.Parameter(
                name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation
            )
            for name, annotation in annotations.items()
        ]
    )
    signature.__annotations__ = annotations
    signature.__name__ = spec.name
    derived = function_schema(signature, GenerateToolJsonSchema, takes_ctx=True)
    return dataclasses.replace(
        derived,
        function=call,
        description=spec.description,
        json_schema=spec.json_schema,
    )


def _describing(
    spec: ToolSpec, runtime: ToolRuntime
) -> Callable[[RunContext[ToolContext], ToolDefinition], Awaitable[ToolDefinition]]:
    async def prepare(
        ctx: RunContext[ToolContext], tool_def: ToolDefinition
    ) -> ToolDefinition:
        return dataclasses.replace(
            tool_def, description=await runtime.description(spec, ctx.deps)
        )

    return prepare


__all__ = ["as_tool_return", "function_toolset"]
