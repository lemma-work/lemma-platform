"""Dynamic function and agent tools for pod agents."""

from __future__ import annotations

import asyncio
import json
from functools import partial
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from pydantic import TypeAdapter
from pydantic_ai._function_schema import FunctionSchema
from pydantic_ai._json_schema import InlineDefsJsonSchemaTransformer
from pydantic_ai.tools import RunContext, Tool
from pydantic_ai.toolsets import FunctionToolset
from pydantic_core import SchemaValidator

from app.core.config import settings
from app.core.log.log import get_logger
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import Permissions
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_job_queue import get_streaq_job_queue
from app.modules.agent.domain.entities import Agent
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
    RunAsWorkload,
)
from app.modules.function.infrastructure.repositories import (
    FunctionRepository,
    FunctionRunRepository,
)
from app.modules.function.services.function_file_manager import FunctionFileManager
from app.modules.function.services.function_service import FunctionService
from app.modules.icon.services.icon_service import IconService
from app.modules.workspace.services.workspace_tool_runtime import (
    WorkspaceToolRuntime,
    get_function_workspace_runtime,
)


logger = get_logger(__name__)

_SUBAGENT_TOOL_TIMEOUT_SECONDS = 300


def _normalize_json_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}, "additionalProperties": True}
    normalized = dict(schema)
    if normalized.get("type") != "object":
        normalized["type"] = "object"
    normalized.setdefault("properties", {})
    normalized.setdefault("additionalProperties", True)
    return normalized


def _inline_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return InlineDefsJsonSchemaTransformer(schema, strict=False).walk()


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
    normalized = _normalize_json_schema(schema)
    return json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))


class AgentCallableToolFactory:
    """Creates dynamic tools configured on an Agent."""

    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory

    async def build_toolsets(
        self,
        *,
        agent: Agent,
        allow_subagents: bool = True,
    ) -> list[FunctionToolset[BaseAgentContext]]:
        if agent.pod_id is None or agent.id is None:
            return []

        tools: list[Tool] = []
        async with self.uow_factory() as uow:
            function_repo = FunctionRepository(uow)
            agent_repo = AgentRepository(uow)
            function_ids, agent_ids = await self._load_callable_resource_ids(
                uow,
                pod_id=agent.pod_id,
                agent_id=agent.id,
            )

            for function_id in function_ids:
                function = await function_repo.get(function_id)
                if function is None or function.status != FunctionStatus.READY:
                    continue
                try:
                    tools.append(self._build_function_tool(function, parent_agent=agent))
                except Exception:
                    logger.warning(
                        "Skipping function tool %s for agent %s: build failed",
                        function.name,
                        agent.name,
                    )

            # agent_<name> tools spawn child conversations, so they only exist on
            # top-level runs (depth=1). Child sub-agent runs keep their function
            # tools but cannot launch further agents.
            if allow_subagents:
                for child_agent_id in agent_ids:
                    child_agent = await agent_repo.get(child_agent_id)
                    if child_agent is None:
                        continue
                    tools.append(self._build_agent_tool(child_agent, parent_agent=agent))

        if not tools:
            return []
        # The sub-agent control toolset (spawn/await/list/stop/...) is wired as the
        # AgentToolset.SUBAGENTS enum and resolved by RunToolAssembler — not
        # appended here — so it is opt-in for user agents and gated to top-level
        # conversations.
        return [FunctionToolset[BaseAgentContext](tools=tools)]

    async def _load_callable_resource_ids(
        self,
        uow,
        *,
        pod_id: UUID,
        agent_id: UUID,
    ) -> tuple[list[UUID], list[UUID]]:
        from sqlalchemy import select

        stmt = select(
            ResourcePermissionGrantModel.resource_type,
            ResourcePermissionGrantModel.resource_id,
        ).where(
            ResourcePermissionGrantModel.pod_id == pod_id,
            ResourcePermissionGrantModel.grantee_type == "AGENT",
            ResourcePermissionGrantModel.grantee_id == agent_id,
            ResourcePermissionGrantModel.permission_id.in_(
                [Permissions.FUNCTION_EXECUTE, Permissions.AGENT_EXECUTE]
            ),
        )
        rows = list((await uow.session.execute(stmt)).all())
        function_ids = [
            resource_id
            for resource_type, resource_id in rows
            if resource_type == "function"
        ]
        agent_ids = [
            resource_id
            for resource_type, resource_id in rows
            if resource_type == "agent" and resource_id != agent_id
        ]
        return function_ids, agent_ids

    def _build_function_tool(self, function: FunctionEntity, *, parent_agent: Agent) -> Tool:
        schema = _inline_schema(_normalize_json_schema(function.input_schema))
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
            # Build a delegated-workload context so execute_function authorizes
            # the call as the agent (which holds the function.execute grant),
            # acting on behalf of the user. Execution needs only function.execute
            # — the agent need not also be granted function.read. Build it in a
            # SHORT UoW and release the connection before executing: a synchronous
            # (API-type) function runs in a sandbox for up to
            # _API_FUNCTION_TIMEOUT_SECONDS and must not pin a pooled worker
            # connection for that whole round-trip.
            async with self.uow_factory() as uow:
                auth_ctx = await AuthorizationDataService(
                    uow.session
                ).build_delegated_workload_context(
                    user_id=ctx.deps.user_id,
                    principal_type="AGENT",
                    principal_id=parent_agent_id,
                    pod_id=function.pod_id,
                    delegation_scope=frozenset([Permissions.FUNCTION_EXECUTE]),
                    delegation_actor_name=parent_agent_name,
                )

            # Reuse the agent's cached workspace token instead of minting a
            # separate function-workload token.
            run_as_workload = RunAsWorkload(
                workload_type=ctx.deps.workload_type or "agent",
                workload_id=parent_agent_id,
                workload_name=parent_agent_name,
            )
            # Factory-mode service: scopes its run-row writes in short UoWs, so no
            # connection is held during the sandbox execution. The run row (and
            # the JOB enqueue event) commit inside the service's own short UoW.
            service = self._build_function_service_with_factory()
            run = await service.execute_function(
                pod_id=function.pod_id,
                name=function.name,
                input_data=dict(request),
                user_id=ctx.deps.user_id,
                ctx=auth_ctx,
                run_as_workload=run_as_workload,
            )

            # JOB functions enqueue a background run and return PENDING; await it.
            if function.type == FunctionType.JOB and run.status in (
                FunctionRunStatus.PENDING,
                FunctionRunStatus.RUNNING,
            ):
                run = await self._await_function_run(run.id)

            if run.status != FunctionRunStatus.COMPLETED:
                raise RuntimeError(run.error or f"Function {function.name} failed")
            return run.output_data or {}

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
            _inline_schema(_normalize_json_schema(agent.input_schema))
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
        """Poll a JOB function run until it reaches a terminal state (bounded)."""
        terminal = {
            FunctionRunStatus.COMPLETED,
            FunctionRunStatus.FAILED,
            FunctionRunStatus.CANCELLED,
        }
        run: FunctionRunEntity | None = None
        interval = settings.function_run_poll_interval_seconds
        attempts = max(1, int(_SUBAGENT_TOOL_TIMEOUT_SECONDS / interval))
        for _ in range(attempts):
            async with self.uow_factory() as uow:
                run = await FunctionRunRepository(uow).get_run(run_id)
            if run is not None and run.status in terminal:
                return run
            await asyncio.sleep(interval)
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
        return (
            f"{prefix}\n"
            f"Input schema: {_schema_preview(function.input_schema)}\n"
            f"Output schema: {_schema_preview(function.output_schema)}"
        )

    def _build_agent_description(self, agent: Agent) -> str:
        prefix = agent.description or f"Execute agent `{agent.name}`."
        return (
            f"{prefix}\n"
            f"Input schema: {_schema_preview(agent.input_schema)}\n"
            f"Output schema: {_schema_preview(agent.output_schema)}"
        )

    def _function_storage_factory(self):
        if settings.effective_storage_backend() == "gcs":
            if not settings.gcs_storage_bucket:
                raise ValueError("GCS storage requires GCS_STORAGE_BUCKET")
            return partial(
                FunctionFileManager,
                bucket_name=settings.gcs_storage_bucket,
            )
        return partial(
            FunctionFileManager,
            root_path=Path(settings.local_file_storage_root) / "common",
        )

    def _build_function_service_with_factory(self) -> FunctionService:
        """Factory-mode FunctionService for executing a function as an agent tool.

        Scopes its own short UoWs, so a synchronous (API-type) function's sandbox
        round-trip does not hold a pooled DB connection in the worker.
        Authorization is carried by the delegated ``ctx`` passed to
        ``execute_function``.
        """
        return FunctionService(
            function_repository=None,
            run_repository=None,
            workspace_service=self._build_workspace_service(),
            storage_factory=self._function_storage_factory(),
            job_queue=get_streaq_job_queue(),
            icon_service=IconService(),
            authorization_service=None,
            uow_factory=self.uow_factory,
        )

    def _build_workspace_service(self) -> WorkspaceToolRuntime:
        return get_function_workspace_runtime()

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
