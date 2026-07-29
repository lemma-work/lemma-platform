"""Shared resolver + invoker for a single agent tool call.

`AgentToolDispatcher` is the one place that knows how to turn an (agent,
conversation, context) into the live set of pydantic-ai tools and how to invoke
one by name. The conversation/pod MCP services and the approval executor all go
through it, so tool discovery and dispatch never drift apart.
"""

from __future__ import annotations

import inspect
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_ai.usage import RunUsage

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import JsonObject, to_json_value
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.tool_assembler import RunToolAssembler


# Fallback when a tool advertises no explicit retry budget. The dispatcher does a
# single invocation (the remote harness/approval path manages its own retries), so this
# only keeps the standalone RunContext well-formed if a tool raises ModelRetry.
_DEFAULT_TOOL_MAX_RETRIES = 5


class UnknownToolError(ValueError):
    """Raised when a tool name is not available for the agent/conversation."""


@dataclass(slots=True)
class PreparedTool:
    name: str
    toolset: AbstractToolset[Any]
    tool: ToolsetTool[Any]
    run_ctx: RunContext[Any]


@dataclass(slots=True)
class ToolInfo:
    name: str
    description: str
    input_schema: JsonObject


class AgentToolDispatcher:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory
        self._assembler = RunToolAssembler(uow_factory)

    async def list_tools(
        self,
        *,
        ctx: BaseAgentContext,
        agent: Agent | None = None,
        conversation: Conversation | None = None,
        toolsets: list[object] | None = None,
        agent_run_id: UUID | None = None,
    ) -> list[ToolInfo]:
        async with AsyncExitStack() as exit_stack:
            prepared = await self._prepare(
                agent=agent,
                conversation=conversation,
                toolsets=toolsets,
                ctx=ctx,
                agent_run_id=agent_run_id,
                exit_stack=exit_stack,
            )
            return [
                ToolInfo(
                    name=tool.name,
                    description=tool.tool.tool_def.description or "",
                    input_schema=dict(
                        to_json_value(tool.tool.tool_def.parameters_json_schema)
                    ),
                )
                for tool in prepared.values()
            ]

    async def call_tool(
        self,
        *,
        ctx: BaseAgentContext,
        name: str,
        arguments: dict[str, Any] | None,
        agent: Agent | None = None,
        conversation: Conversation | None = None,
        toolsets: list[object] | None = None,
        agent_run_id: UUID | None = None,
    ) -> object:
        async with AsyncExitStack() as exit_stack:
            prepared = await self._prepare(
                agent=agent,
                conversation=conversation,
                toolsets=toolsets,
                ctx=ctx,
                agent_run_id=agent_run_id,
                exit_stack=exit_stack,
            )
            tool = prepared.get(name)
            if tool is None:
                raise UnknownToolError(name)
            tool_ctx = self._tool_call_context(tool, agent_run_id)
            validated = await self._validate_arguments(
                tool=tool,
                arguments=arguments or {},
                run_ctx=tool_ctx,
            )
            return await tool.toolset.call_tool(
                tool.name,
                validated,
                tool_ctx,
                tool.tool,
            )

    async def _prepare(
        self,
        *,
        agent: Agent | None,
        conversation: Conversation | None,
        ctx: BaseAgentContext,
        agent_run_id: UUID | None,
        exit_stack: AsyncExitStack,
        toolsets: list[object] | None = None,
    ) -> dict[str, PreparedTool]:
        if toolsets is None:
            toolsets = await self._assembler.assemble(
                agent=agent,
                conversation=conversation,
            )
        run_ctx = self._run_context(ctx, agent_run_id)
        prepared: dict[str, PreparedTool] = {}
        for raw_toolset in toolsets:
            if not isinstance(raw_toolset, AbstractToolset):
                continue
            toolset = await raw_toolset.for_run(run_ctx)
            await exit_stack.enter_async_context(toolset)
            for original_name, tool in (await toolset.get_tools(run_ctx)).items():
                name = tool.tool_def.name or original_name
                if name in prepared:
                    raise ValueError(f"Duplicate tool name: {name}")
                prepared[name] = PreparedTool(
                    name=name,
                    toolset=toolset,
                    tool=tool,
                    run_ctx=run_ctx,
                )
        return prepared

    def _run_context(
        self,
        ctx: BaseAgentContext,
        agent_run_id: UUID | None,
    ) -> RunContext[Any]:
        return RunContext(
            deps=ctx,
            model=None,  # type: ignore[arg-type]
            usage=RunUsage(),
            prompt=None,
            retries={},
            run_id=str(agent_run_id) if agent_run_id is not None else None,
            metadata={"agent_run_id": str(agent_run_id) if agent_run_id else None},
            model_settings=None,
        )

    def _tool_call_context(
        self,
        tool: PreparedTool,
        agent_run_id: UUID | None,
    ) -> RunContext[Any]:
        max_retries = tool.tool.max_retries
        if max_retries is None:
            max_retries = _DEFAULT_TOOL_MAX_RETRIES
        return replace(
            tool.run_ctx,
            usage=RunUsage(tool_calls=1),
            retries={tool.name: 0},
            tool_name=tool.name,
            retry=0,
            max_retries=max_retries,
            run_id=str(agent_run_id) if agent_run_id is not None else None,
        )

    async def _validate_arguments(
        self,
        *,
        tool: PreparedTool,
        arguments: dict[str, Any],
        run_ctx: RunContext[Any],
    ) -> Any:
        validated = tool.tool.args_validator.validate_python(
            arguments,
            context=run_ctx.validation_context,
        )
        if tool.tool.args_validator_func is not None:
            if isinstance(validated, dict):
                validation_result = tool.tool.args_validator_func(
                    run_ctx,
                    **validated,
                )
            else:
                validation_result = tool.tool.args_validator_func(
                    run_ctx,
                    validated,
                )
            if inspect.isawaitable(validation_result):
                await validation_result
        return validated
