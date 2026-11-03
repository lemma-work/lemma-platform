"""A pod's functions, as tools an MCP host such as ChatGPT or Claude calls.

Each function the person may run is a tool of its own -- its input schema the
tool's, its description the tool's -- the way a Lemma agent is given a function
it holds a grant on. A function *is* a tool, and a host checks arguments
against a schema better than a model reads one out of a description. See
``docs/architecture/mcp-plugin.md`` §f.

The run is the person's, through the pod's own assistant
(``function.contracts.person_runs``): the person's access decides whether it
may run, the function's own grants what it touches, as for a run started in
Lemma. A JOB function is waited on briefly and otherwise handed back as still
running, for ``lemma_pod_function_run`` to read later.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from mcp.types import CallToolResult, Tool, ToolAnnotations
from pydantic import BaseModel, ValidationError

from app.core.authorization.factory import create_authorization_data_service
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.mcp import resource_tool_name
from app.modules.agent.services.mcp_content import tool_call_result
from app.modules.agent.services.poll_backoff import poll_until
from app.modules.agent.tools.callable_tool_factory import inline_tool_schema_refs
from app.modules.agent.tools.tool_payload_limits import bounded_tool_payload
from app.modules.function.contracts import (
    FunctionEntity,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionType,
)
from app.modules.mcp_access.contracts import McpPrincipal, Scope

RUN_FUNCTION_PREFIX = "lemma_run_"
FUNCTION_RUN = "lemma_pod_function_run"
#: The same ceiling as apps and agents.
MAX_FUNCTIONS = 20
#: How long a JOB function is waited on before it is handed back as running.
JOB_WAIT_SECONDS = 25.0
_POLL_SECONDS = 1.0

_STILL_RUNNING = frozenset({FunctionRunStatus.PENDING, FunctionRunStatus.RUNNING})

_RUN_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    # Nothing says what a function does: it may write, delete, or reach any
    # service it holds a key for, so the host asks the person before each run.
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=True,
)
_READ_RUN_ANNOTATIONS = ToolAnnotations(
    title="Check on a function run",
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


@dataclass(frozen=True, slots=True)
class RunnableFunction:
    name: str
    description: str | None
    input_schema: JsonObject
    is_job: bool

    @property
    def tool_name(self) -> str:
        return resource_tool_name(RUN_FUNCTION_PREFIX, self.name)

    @classmethod
    def of(cls, function: FunctionEntity) -> RunnableFunction:
        return cls(
            name=function.name,
            description=function.description,
            input_schema=dict(function.input_schema or {}),
            is_job=function.type is FunctionType.JOB,
        )


@dataclass(frozen=True, slots=True)
class PersonFunctionRun:
    """One of the person's runs, with the name of the function it ran."""

    function: str
    run: FunctionRunEntity


class FunctionGateway(Protocol):
    """What the tools need of the function module, for one person in one pod."""

    async def runnable(
        self, *, pod_id: UUID, user_id: UUID
    ) -> list[RunnableFunction]: ...

    async def run(
        self, *, pod_id: UUID, name: str, input_data: JsonObject, user_id: UUID
    ) -> FunctionRunEntity: ...

    async def read_run(
        self, *, pod_id: UUID, run_id: UUID, user_id: UUID
    ) -> PersonFunctionRun | None: ...


class FunctionRunArguments(BaseModel):
    run_id: UUID
    wait_seconds: float = 0.0


def offers_functions(principal: McpPrincipal | None) -> bool:
    """A function may do anything, so running one needs a connection allowed to
    change things."""
    return principal is not None and Scope.WRITE in principal.scopes


def is_function_tool(name: str) -> bool:
    return name.startswith(RUN_FUNCTION_PREFIX) or name == FUNCTION_RUN


def function_tools(functions: Sequence[RunnableFunction]) -> list[Tool]:
    if not functions:
        return []
    tools = [
        Tool(
            name=function.tool_name,
            title=f"Run {function.name}",
            description=(
                function.description or f"Run the pod's `{function.name}` function."
            )
            + " It runs as the person, in this pod.",
            input_schema=_object_schema(function.input_schema),
            annotations=_RUN_ANNOTATIONS.model_copy(
                update={"title": f"Run {function.name}"}
            ),
        )
        for function in functions[:MAX_FUNCTIONS]
    ]
    return [
        *tools,
        Tool(
            name=FUNCTION_RUN,
            title=_READ_RUN_ANNOTATIONS.title,
            description=(
                "Read a function run that was still going when its tool returned: "
                "its status, and its output once it has finished."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "string", "format": "uuid"},
                    "wait_seconds": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": JOB_WAIT_SECONDS,
                        "default": 0,
                    },
                },
                "required": ["run_id"],
            },
            annotations=_READ_RUN_ANNOTATIONS,
        ),
    ]


def _object_schema(schema: JsonObject) -> JsonObject:
    """The function's input schema as an MCP tool's: an object, refs inlined.

    A function with no input, or one declared as anything but an object, takes
    no arguments here rather than a schema MCP refuses.
    """
    if schema.get("type") != "object":
        return {"type": "object", "properties": {}}
    return inline_tool_schema_refs(schema)


def run_payload(function_name: str, run: FunctionRunEntity) -> JsonObject:
    """What a function tool answers, given where its run got to.

    A JOB run still going is not a failure: reported as one, the model's
    reasonable answer is to run it again, repeating whatever the function does.
    """
    base: JsonObject = {"function": function_name, "run_id": str(run.id)}
    if run.status in _STILL_RUNNING:
        return {
            **base,
            "success": True,
            "status": run.status.value,
            "message": (
                f"{function_name} is still running. It has not failed: do not run "
                f"it again. Call {FUNCTION_RUN} with this run_id for its result."
            ),
        }
    if run.status is not FunctionRunStatus.COMPLETED:
        return {
            **base,
            "success": False,
            "status": run.status.value,
            "error": run.error or f"{function_name} did not finish.",
        }
    return {
        **base,
        "success": True,
        "status": run.status.value,
        "output": bounded_tool_payload(run.output_data or {}, what="function output"),
    }


class PodFunctions:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        gateway: FunctionGateway | None = None,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    ) -> None:
        self._gateway = gateway or _FunctionModule(uow_factory)
        self._sleep = sleep

    async def functions(self, *, pod_id: UUID, user_id: UUID) -> list[RunnableFunction]:
        return await self._gateway.runnable(pod_id=pod_id, user_id=user_id)

    async def call(
        self, name: str, arguments: JsonObject, *, principal: McpPrincipal
    ) -> CallToolResult:
        if name == FUNCTION_RUN:
            return await self._read(arguments, principal=principal)
        functions = await self.functions(
            pod_id=principal.pod_id, user_id=principal.user_id
        )
        function = next((one for one in functions if one.tool_name == name), None)
        if function is None:
            return _refusal("That function is not in this pod, or you may not run it.")
        try:
            run = await self._gateway.run(
                pod_id=principal.pod_id,
                name=function.name,
                input_data=dict(arguments),
                user_id=principal.user_id,
            )
        except DomainError as exc:
            # Not allowed, the input does not fit the schema, or the function
            # could not start: each says why.
            return _refusal(exc.message)
        if run.status in _STILL_RUNNING:
            run = await self._wait(run, principal=principal, wait=JOB_WAIT_SECONDS)
        return tool_call_result(run_payload(function.name, run))

    async def _read(
        self, arguments: JsonObject, *, principal: McpPrincipal
    ) -> CallToolResult:
        try:
            request = FunctionRunArguments.model_validate(arguments)
        except ValidationError as exc:
            return _refusal(str(exc.errors()[0].get("msg", "is not valid")))
        found = await self._gateway.read_run(
            pod_id=principal.pod_id, run_id=request.run_id, user_id=principal.user_id
        )
        if found is None:
            return _refusal("No function run of yours in this pod has that id.")
        run = found.run
        if run.status in _STILL_RUNNING and request.wait_seconds > 0:
            run = await self._wait(
                run,
                principal=principal,
                wait=min(request.wait_seconds, JOB_WAIT_SECONDS),
            )
        return tool_call_result(run_payload(found.function, run))

    async def _wait(
        self, run: FunctionRunEntity, *, principal: McpPrincipal, wait: float
    ) -> FunctionRunEntity:
        run_id = run.id
        if run_id is None:
            return run

        async def settled() -> FunctionRunEntity | None:
            current = await self._gateway.read_run(
                pod_id=principal.pod_id, run_id=run_id, user_id=principal.user_id
            )
            return (
                current.run
                if current is not None and current.run.status not in _STILL_RUNNING
                else None
            )

        done = await poll_until(
            settled, wait_seconds=wait, base_seconds=_POLL_SECONDS, sleep=self._sleep
        )
        return done or run


class _FunctionModule:
    """The gateway over the function module's contracts."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self.uow_factory = uow_factory

    async def runnable(self, *, pod_id: UUID, user_id: UUID) -> list[RunnableFunction]:
        from app.modules.function.contracts.person_runs import runnable_functions

        async with self.uow_factory() as uow:
            ctx = await create_authorization_data_service(uow).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            functions = await runnable_functions(
                uow, pod_id=pod_id, ctx=ctx, limit=MAX_FUNCTIONS * 5
            )
        return [RunnableFunction.of(function) for function in functions][:MAX_FUNCTIONS]

    async def run(
        self, *, pod_id: UUID, name: str, input_data: JsonObject, user_id: UUID
    ) -> FunctionRunEntity:
        from app.modules.function.contracts.person_runs import run_function_as_person

        return await run_function_as_person(
            self.uow_factory,
            pod_id=pod_id,
            name=name,
            input_data=input_data,
            user_id=user_id,
        )

    async def read_run(
        self, *, pod_id: UUID, run_id: UUID, user_id: UUID
    ) -> PersonFunctionRun | None:
        from app.modules.function.contracts.agent_tools import (
            get_function_by_id,
            get_function_run,
        )

        async with self.uow_factory() as uow:
            run = await get_function_run(uow, run_id)
            if run is None or run.user_id != user_id:
                return None
            function = await get_function_by_id(uow, run.function_id)
        if function is None or function.pod_id != pod_id:
            return None
        return PersonFunctionRun(function=function.name, run=run)


def _refusal(message: str) -> CallToolResult:
    return tool_call_result({"success": False, "error": message})
