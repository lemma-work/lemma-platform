"""Dynamic function and agent tools for pod agents."""

from __future__ import annotations

from contextlib import suppress
import asyncio
import dataclasses
import json
from collections.abc import Sequence
from typing import Any, Literal
from uuid import UUID

from app.modules.agent.domain.tools import ToolGroup, ToolPolicy, ToolSpec
from app.modules.agent.infrastructure.providers.tool_schemas import (
    inline_schema,
    normalize_json_schema,
)
from app.modules.agent.infrastructure.tools.payload_limits import bounded_tool_payload

from app.modules.agent.config import agent_settings
from app.core.log.log import get_logger
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.entities import Agent
from app.modules.agent.infrastructure.mcp import (
    MAX_LOCAL_TOOL_NAME_LENGTH,
    resource_tool_name,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
)
from app.modules.agent.services.poll_backoff import poll_delay
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.toolsets import AgentGrantSummary
from app.modules.function.contracts import (
    FunctionEntity,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
)
from app.modules.function.contracts import agent_tools as function_tools


logger = get_logger(__name__)

_SUBAGENT_TOOL_TIMEOUT_SECONDS = 300


def _with_output_schema(prefix: str, output_schema: dict[str, Any] | None) -> str:
    """Append the output schema to a dynamic tool's description, if it has one."""
    preview = _schema_preview(output_schema)
    return f"{prefix}\nOutput schema: {preview}" if preview else prefix


# Plain agents (no input_schema) are exposed as a single-string-input tool.
_SINGLE_INPUT_FIELD = "input"


def _single_string_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            _SINGLE_INPUT_FIELD: {
                "type": "string",
                "description": "The task or question for the agent, in natural language.",
            }
        },
        "required": [_SINGLE_INPUT_FIELD],
        "additionalProperties": False,
    }


def _schema_preview(schema: dict[str, Any] | None) -> str:
    """Render a schema for a tool *description*, or "" when there is nothing to say.

    Only the OUTPUT schema goes in a description. The input schema is already the
    tool's ``parameters_json_schema``, so repeating it here billed every dynamic
    function/agent tool for its input schema twice. An absent schema renders as
    empty rather than as ``{"type":"object","properties":{},...}`` — 61 characters
    of boilerplate that told the model nothing.
    """
    if not isinstance(schema, dict) or not schema:
        return ""
    return json.dumps(
        normalize_json_schema(schema), ensure_ascii=False, separators=(",", ":")
    )


_STILL_RUNNING = frozenset({FunctionRunStatus.PENDING, FunctionRunStatus.RUNNING})


def function_tool_result(function_name: str, run: FunctionRunEntity) -> dict[str, Any]:
    """What a function tool call returns, given where its run got to.

    A JOB run still going when the wait ran out is not a failure. It used to be
    reported as "Function X failed", and the model's reasonable answer to a
    failure is to run it again -- repeating whatever the function does, while
    the first run carried on doing it.
    """
    if run.status in _STILL_RUNNING:
        return {
            "run_id": str(run.id),
            "status": run.status.value,
            "still_running": True,
            "note": (
                f"`{function_name}` is still running after "
                f"{_SUBAGENT_TOOL_TIMEOUT_SECONDS // 60} minutes. It has not "
                "failed -- do not run it again, which would repeat what it "
                "does. Check its result later with `lemma functions runs get "
                f"{function_name} {run.id}`, or tell the person it is running."
            ),
        }
    if run.status != FunctionRunStatus.COMPLETED:
        raise RuntimeError(run.error or f"Function {function_name} failed")
    return bounded_tool_payload(run.output_data or {}, what="function output")


class AgentCallableToolFactory:
    """Creates dynamic tools configured on an Agent."""

    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory

    async def build_specs(
        self,
        *,
        agent: Agent,
        allow_subagents: bool = True,
        grants: AgentGrantSummary | None = None,
    ) -> tuple[ToolSpec, ...]:
        """``function_<name>`` / ``agent_<name>`` tools for this agent's grants.

        ``grants`` is the summary the caller already loaded for toolset
        selection; passing it through spares a second read of the same rows.
        """
        if agent.pod_id is None or agent.id is None:
            return ()

        tools: list[ToolSpec] = []
        async with self.uow_factory() as uow:
            agent_repo = AgentRepository(uow)
            if grants is None:
                grants = await self._load_grant_summary(
                    uow, pod_id=agent.pod_id, agent_id=agent.id
                )
            function_ids, agent_ids = grants.function_ids, grants.agent_ids

            # One read for the granted functions and one for the child agents,
            # rather than one per grant. This assembles the toolset before every
            # agent turn, so an agent with twenty grants paid twenty round trips
            # to learn what it is allowed to call -- each of them an id it
            # already held.
            functions = await function_tools.get_functions_by_ids(uow, function_ids)
            for function_id in function_ids:
                function = functions.get(function_id)
                if function is None or function.status != FunctionStatus.READY:
                    continue
                with suppress(Exception):
                    tools.append(
                        self._build_function_tool(function, parent_agent=agent)
                    )

            # agent_<name> tools spawn child conversations, so they only exist on
            # top-level runs (depth=1). Child sub-agent runs keep their function
            # tools but cannot launch further agents.
            if allow_subagents:
                child_agents = await agent_repo.get_many(agent_ids)
                for child_agent_id in agent_ids:
                    child_agent = child_agents.get(child_agent_id)
                    if child_agent is None:
                        continue
                    tools.append(
                        self._build_agent_tool(child_agent, parent_agent=agent)
                    )

        # The sub-agent controls (spawn/interact/query) are the SUBAGENTS
        # toolset, not these: opt-in for user agents, top-level runs only.
        return tuple(distinct_tool_names(tools))

    async def load_grant_summary(
        self, *, pod_id: UUID, agent_id: UUID
    ) -> AgentGrantSummary:
        """Everything one agent's grants decide, in one query.

        Two consumers need this and they used to be able to disagree: the
        callable tools below, and the toolset selection that now derives POD and
        CONNECTORS from a grant rather than a second switch. Reading the table
        once and handing the same answer to both is what keeps a tool from being
        listed by one path and refused by the other.

        Not filtered by permission id, unlike the callable-tool query it
        replaces: "does this agent hold *any* grant on a folder" is a different
        question from "may it execute this function", and a read-only grant
        still means the agent should be able to reach pod data.
        """
        async with self.uow_factory() as uow:
            return await self._load_grant_summary(uow, pod_id=pod_id, agent_id=agent_id)

    async def _load_grant_summary(
        self,
        uow,
        *,
        pod_id: UUID,
        agent_id: UUID,
    ) -> AgentGrantSummary:
        from sqlalchemy import select

        stmt = select(
            ResourcePermissionGrantModel.resource_type,
            ResourcePermissionGrantModel.resource_id,
            ResourcePermissionGrantModel.permission_id,
        ).where(
            ResourcePermissionGrantModel.pod_id == pod_id,
            ResourcePermissionGrantModel.grantee_type == "AGENT",
            ResourcePermissionGrantModel.grantee_id == agent_id,
        )
        rows = list((await uow.session.execute(stmt)).all())
        return grant_summary_of(rows, agent_id=agent_id)

    def _build_function_tool(
        self, function: FunctionEntity, *, parent_agent: Agent
    ) -> ToolSpec:
        schema = inline_schema(normalize_json_schema(function.input_schema))
        description = self._build_function_description(function)
        function_name = resource_tool_name("function_", function.name)
        parent_agent_id = parent_agent.id
        parent_agent_name = parent_agent.name

        # The tool's JSON schema is the function's own flat input schema, so the
        # model's arguments arrive as one object of top-level fields.
        async def _run_function(
            ctx: ToolContext, request: dict[str, Any]
        ) -> dict[str, Any]:
            # The use case authorizes the call as the agent (which holds the
            # function.execute grant) on behalf of the user. It builds the
            # delegated-workload context AND runs the DB resolve phase inside ONE
            # short UoW (so ctx.require's resource hydration never touches a closed
            # session), then runs the sandbox with no pooled connection held.
            # run_as_workload stays None so the function executes under its own
            # FUNCTION principal with its own grants — the same identity as the
            # direct-user and JOB paths. Exposing a function as an agent tool
            # therefore needs exactly ONE grant on the parent (function.execute);
            # the function's resource grants are never mirrored onto the agent.
            run = await function_tools.execute_function_for_agent(
                self.uow_factory,
                pod_id=function.pod_id,
                name=function.name,
                input_data=dict(request),
                user_id=ctx.user_id,
                agent_id=parent_agent_id,
                agent_name=parent_agent_name,
                # Minimal single-operation scope; implication-expanded, so the
                # implied function.read is admitted (see delegation.py).
                delegation_scope=frozenset([Permissions.FUNCTION_EXECUTE]),
            )

            # JOB functions enqueue a background run and return PENDING; await it.
            if function.type == FunctionType.JOB and run.status in (
                FunctionRunStatus.PENDING,
                FunctionRunStatus.RUNNING,
            ):
                run = await self._await_function_run(run.id)

            return function_tool_result(function.name, run)

        return _dynamic_spec(
            function_name, description, schema, _run_function, kind="function"
        )

    def _build_agent_tool(self, agent: Agent, *, parent_agent: Agent) -> ToolSpec:
        del parent_agent  # grant is enforced in SubAgentService.spawn
        # A plain agent (no input_schema) is exposed as a single-string-input
        # tool; one with an input_schema takes that schema's fields as flat kwargs.
        has_input_schema = bool(agent.input_schema)
        has_output_schema = bool(agent.output_schema)
        schema = (
            inline_schema(normalize_json_schema(agent.input_schema))
            if has_input_schema
            else _single_string_input_schema()
        )
        description = self._build_agent_description(agent)
        agent_tool_name = resource_tool_name("agent_", agent.name)

        # See _build_function_tool: the arguments are one flat object.
        async def _run_agent(
            ctx: ToolContext, request: dict[str, Any]
        ) -> dict[str, Any] | str:
            if ctx.agent_name == agent.name:
                raise RuntimeError(f"Agent {agent.name} cannot call itself as a tool")
            # Spawn a real, persisted child conversation linked to the parent
            # (parent_id + parent_run_id) and run it via the job queue, then wait
            # for the result. The parent's agent.execute grant is enforced inside
            # SubAgentService.spawn. Lazy import avoids a tool-registry cycle.
            from app.modules.agent.services.subagent_service import SubAgentService

            input_data = (
                dict(request)
                if has_input_schema
                else {_SINGLE_INPUT_FIELD: request.get(_SINGLE_INPUT_FIELD, "")}
            )
            service = SubAgentService(self.uow_factory)
            handle = await service.spawn(
                ctx,
                agent_name=agent.name,
                input_data=input_data,
            )
            result = await service.await_run(
                ctx,
                conversation_id=handle.conversation_id,
                run_id=handle.run_id,
                timeout_seconds=_SUBAGENT_TOOL_TIMEOUT_SECONDS,
            )
            if result.get("timed_out"):
                # Keep the resume handle even in string mode — a bare string would
                # drop the conversation_id/run_id the parent needs to continue.
                return {
                    "conversation_id": str(handle.conversation_id),
                    "run_id": str(handle.run_id),
                    "status": result.get("status"),
                    "note": (
                        "Sub-agent still running. Call `wait_for` with "
                        "`subagent_run_id` set to this run_id and end your turn; "
                        "you wake with its result."
                    ),
                }
            output = result.get("output")
            if not has_output_schema:
                # Plain agent → return the final answer as a string. A no-schema
                # run stores its output as {"answer": <text>}
                # (RunMessageWriter.output_data_from_event), so unwrap that.
                if isinstance(output, dict) and "answer" in output:
                    return str(output["answer"])
                if output is None:
                    return str(result.get("error") or result.get("status") or "")
                return output if isinstance(output, str) else str(output)
            if isinstance(output, dict):
                return output
            return {
                "status": result.get("status"),
                "output": output,
                "error": result.get("error"),
            }

        return _dynamic_spec(
            agent_tool_name, description, schema, _run_agent, kind="agent"
        )

    async def _await_function_run(self, run_id: UUID) -> FunctionRunEntity:
        """Poll a JOB function run until it reaches a terminal state (bounded).

        A deadline rather than a fixed attempt count, because the pause grows:
        at the configured interval a five-minute wait was six hundred unit-of-
        work checkouts, all of them asking the same question.
        """
        terminal = {
            FunctionRunStatus.COMPLETED,
            FunctionRunStatus.FAILED,
            FunctionRunStatus.CANCELLED,
        }
        run: FunctionRunEntity | None = None
        interval = agent_settings.function_run_poll_interval_seconds
        loop = asyncio.get_event_loop()
        deadline = loop.time() + _SUBAGENT_TOOL_TIMEOUT_SECONDS
        attempt = 0
        while True:
            async with self.uow_factory() as uow:
                run = await function_tools.get_function_run(uow, run_id)
            if run is not None and run.status in terminal:
                return run
            if loop.time() >= deadline:
                break
            attempt += 1
            await asyncio.sleep(
                poll_delay(
                    attempt,
                    base_seconds=interval,
                    remaining_seconds=deadline - loop.time(),
                )
            )
        if run is None:
            raise RuntimeError(f"Function run {run_id} not found")
        return run

    async def resolve_configured_accounts(
        self,
        *,
        agent: Agent,
        user_id: UUID,
    ) -> dict[str, UUID]:
        _ = agent, user_id
        return {}

    def _build_function_description(self, function: FunctionEntity) -> str:
        prefix = function.description or f"Execute function `{function.name}`."
        return _with_output_schema(prefix, function.output_schema)

    def _build_agent_description(self, agent: Agent) -> str:
        prefix = agent.description or f"Execute agent `{agent.name}`."
        return _with_output_schema(prefix, agent.output_schema)


def _dynamic_spec(
    name: str,
    description: str,
    schema: dict[str, Any],
    run: Any,
    *,
    kind: Literal["function", "agent"],
) -> ToolSpec:
    """A tool whose arguments are a stored JSON schema, not a request model."""
    return ToolSpec(
        name=name,
        description=description,
        parameters=None,
        json_schema=schema,
        toolset=ToolGroup.CALLABLES,
        run=run,
        policy=ToolPolicy(kind=kind),
        strict=False,
    )


async def load_agent_grant_summary(
    uow_factory: UnitOfWorkFactory, *, agent: Agent
) -> AgentGrantSummary:
    """This agent's grant summary, or an empty one where it cannot have grants."""
    if agent.pod_id is None or agent.id is None:
        return AgentGrantSummary()
    return await AgentCallableToolFactory(uow_factory).load_grant_summary(
        pod_id=agent.pod_id, agent_id=agent.id
    )


def distinct_tool_names(tools: list[ToolSpec]) -> list[ToolSpec]:
    """The same tools, no two sharing a name.

    `resource_tool_name` keeps a valid name as it is and gives any other a
    digest suffix, so a resource literally named like another's sanitized form
    can land on the same tool name. A toolset refuses two tools of one name,
    and that refusal failed the whole run before it listed anything. A later
    duplicate takes a counter instead, in grant order, so the name is the same
    from turn to turn.
    """
    seen: set[str] = set()
    distinct: list[ToolSpec] = []
    for tool in tools:
        name, counter = tool.name, 2
        while name in seen:
            suffix = f"_{counter}"
            name = tool.name[: MAX_LOCAL_TOOL_NAME_LENGTH - len(suffix)] + suffix
            counter += 1
        distinct.append(dataclasses.replace(tool, name=name))
        seen.add(name)
    return distinct


def grant_summary_of(
    rows: Sequence[tuple[str, UUID, str]], *, agent_id: UUID
) -> AgentGrantSummary:
    """What ``(resource_type, resource_id, permission_id)`` grant rows decide."""
    callable_rows = [
        (resource_type, resource_id)
        for resource_type, resource_id, permission_id in rows
        if permission_id in (Permissions.FUNCTION_EXECUTE, Permissions.AGENT_EXECUTE)
    ]
    function_ids = tuple(
        resource_id
        for resource_type, resource_id in callable_rows
        if resource_type == "function"
    )
    agent_ids = tuple(
        resource_id
        for resource_type, resource_id in callable_rows
        if resource_type == "agent" and resource_id != agent_id
    )
    return AgentGrantSummary.from_grants(
        [(resource_type, resource_id) for resource_type, resource_id, _ in rows],
        function_ids=function_ids,
        agent_ids=agent_ids,
    )
