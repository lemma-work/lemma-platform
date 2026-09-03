"""Dynamic function and agent tools for pod agents."""

from __future__ import annotations

from contextlib import suppress
import asyncio
import json
from typing import Any, cast
from uuid import UUID

from pydantic import TypeAdapter
from pydantic_ai.tools import RunContext, Tool
from pydantic_ai.toolsets import FunctionToolset
from pydantic_core import SchemaValidator

from app.modules.agent.tools.tool_payload_limits import bounded_tool_payload
from app.modules.agent.infrastructure.pydantic_ai_compat import (
    FunctionSchema,
    InlineDefsJsonSchemaTransformer,
)

from app.modules.agent.config import agent_settings
from app.core.log.log import get_logger
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.entities import Agent
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
)
from app.modules.agent.services.poll_backoff import poll_delay
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.toolset_selection import AgentGrantSummary
from app.modules.function.contracts import (
    FunctionEntity,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
)
from app.composition.agent_functions import (
    create_function_repository,
    create_function_run_repository,
    create_function_use_cases,
)


logger = get_logger(__name__)

_SUBAGENT_TOOL_TIMEOUT_SECONDS = 300


def normalize_json_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}, "additionalProperties": True}
    normalized = dict(schema)
    if normalized.get("type") != "object":
        normalized["type"] = "object"
    normalized.setdefault("properties", {})
    normalized.setdefault("additionalProperties", True)
    return normalized


def inline_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return InlineDefsJsonSchemaTransformer(schema, strict=False).walk()


def inline_tool_schema_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline non-recursive ``$defs``/``$ref`` in a tool's input schema.

    OpenAI-compatible providers (notably Fireworks' GLM) can't resolve
    ``$ref`` -> ``#/$defs/...`` server-side and reject the request with
    ``Error resolving schema reference ... AttributeError("'NoneType' ... lookup")``.
    The pydantic-ai model path already inlines refs via a model profile, but tool
    schemas served over MCP to remote harnesses (Claude Code, Cursor, OpenCode,
    ...) were passed through raw. Inline them here too. Best-effort: a schema the
    transformer can't process falls back to the original.
    """
    if not isinstance(schema, dict):
        return schema
    try:
        return InlineDefsJsonSchemaTransformer(dict(schema), strict=False).walk()
    except Exception:  # noqa: BLE001 -- never break tool listing over a schema quirk
        return dict(schema)


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


class AgentCallableToolFactory:
    """Creates dynamic tools configured on an Agent."""

    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory

    async def build_toolsets(
        self,
        *,
        agent: Agent,
        allow_subagents: bool = True,
        grants: AgentGrantSummary | None = None,
    ) -> list[FunctionToolset[BaseAgentContext]]:
        """``function_<name>`` / ``agent_<name>`` tools for this agent's grants.

        ``grants`` is the summary the caller already loaded for toolset
        selection; passing it through spares a second read of the same rows.
        """
        if agent.pod_id is None or agent.id is None:
            return []

        tools: list[Tool] = []
        async with self.uow_factory() as uow:
            function_repo = create_function_repository(uow)
            agent_repo = AgentRepository(uow)
            if grants is None:
                grants = await self._load_grant_summary(
                    uow, pod_id=agent.pod_id, agent_id=agent.id
                )
            function_ids, agent_ids = grants.function_ids, grants.agent_ids

            for function_id in function_ids:
                function = await function_repo.get(function_id)
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
                for child_agent_id in agent_ids:
                    child_agent = await agent_repo.get(child_agent_id)
                    if child_agent is None:
                        continue
                    tools.append(
                        self._build_agent_tool(child_agent, parent_agent=agent)
                    )

        if not tools:
            return []
        # The sub-agent control toolset (spawn/await/list/stop/...) is wired as the
        # AgentToolset.SUBAGENTS enum and resolved by RunToolAssembler — not
        # appended here — so it is opt-in for user agents and gated to top-level
        # conversations.
        return [FunctionToolset[BaseAgentContext](tools=tools)]

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
        callable_rows = [
            (resource_type, resource_id)
            for resource_type, resource_id, permission_id in rows
            if permission_id
            in (Permissions.FUNCTION_EXECUTE, Permissions.AGENT_EXECUTE)
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

    def _build_function_tool(
        self, function: FunctionEntity, *, parent_agent: Agent
    ) -> Tool:
        schema = inline_schema(normalize_json_schema(function.input_schema))
        description = self._build_function_description(function)
        function_name = f"function_{function.name}"
        parent_agent_id = parent_agent.id
        parent_agent_name = parent_agent.name

        # pydantic-ai invokes this as `_run_function(ctx, **validated_args)`: the
        # tool's JSON schema is the function's flat input schema, so the model's
        # arguments arrive as top-level kwargs (e.g. `apps=...`), not nested under
        # `request`. Collect them via **request so arbitrary input fields bind here
        # instead of raising "unexpected keyword argument".
        async def _run_function(
            ctx: RunContext[BaseAgentContext],
            **request: Any,
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
            use_cases = create_function_use_cases(self.uow_factory)
            run = await use_cases.execute_function_as_workload(
                pod_id=function.pod_id,
                name=function.name,
                input_data=dict(request),
                user_id=ctx.deps.user_id,
                principal_type="AGENT",
                principal_id=parent_agent_id,
                # Minimal single-operation scope; implication-expanded, so the
                # implied function.read is admitted (see delegation.py).
                delegation_scope=frozenset([Permissions.FUNCTION_EXECUTE]),
                delegation_actor_name=parent_agent_name,
            )

            # JOB functions enqueue a background run and return PENDING; await it.
            if function.type == FunctionType.JOB and run.status in (
                FunctionRunStatus.PENDING,
                FunctionRunStatus.RUNNING,
            ):
                run = await self._await_function_run(run.id)

            if run.status != FunctionRunStatus.COMPLETED:
                raise RuntimeError(run.error or f"Function {function.name} failed")
            return bounded_tool_payload(run.output_data or {}, what="function output")

        return Tool(
            _run_function,
            name=function_name,
            description=description,
            takes_ctx=True,
            strict=False,
            function_schema=self._build_dynamic_function_schema(
                name=function_name,
                function=_run_function,
                description=description,
                schema=schema,
            ),
        )

    def _build_agent_tool(self, agent: Agent, *, parent_agent: Agent) -> Tool:
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
        agent_tool_name = f"agent_{agent.name}"

        # See _build_function_tool: model arguments arrive as flat kwargs, so
        # collect them via **request rather than a single `request` parameter.
        async def _run_agent(
            ctx: RunContext[BaseAgentContext],
            **request: Any,
        ) -> dict[str, Any] | str:
            if ctx.deps.agent_name == agent.name:
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
                ctx.deps,
                agent_name=agent.name,
                input_data=input_data,
            )
            result = await service.await_run(
                ctx.deps,
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
                        "Sub-agent still running; poll query_subagents "
                        "(mode='messages') or interact_subagent (action='await') "
                        "to continue."
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

        return Tool(
            _run_agent,
            name=agent_tool_name,
            description=description,
            takes_ctx=True,
            strict=False,
            function_schema=self._build_dynamic_function_schema(
                name=agent_tool_name,
                function=_run_agent,
                description=description,
                schema=schema,
            ),
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
                run = await create_function_run_repository(uow).get_run(run_id)
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

    def _build_dynamic_function_schema(
        self,
        *,
        name: str,
        function,
        description: str,
        schema: dict[str, Any],
    ) -> FunctionSchema:
        validator = cast(
            SchemaValidator,
            TypeAdapter(dict[str, Any]).validator,
        )
        # No single_arg_name: the model's arguments are passed through as flat
        # kwargs to the tool function (which collects them via **request), so there
        # is no single wrapper parameter to advertise.
        return FunctionSchema(
            name=name,
            function=function,
            description=description,
            validator=validator,
            json_schema=schema,
            takes_ctx=True,
            is_async=True,
        )
