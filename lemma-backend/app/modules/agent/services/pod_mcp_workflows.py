"""A pod's workflows, started and followed from an MCP host such as ChatGPT.

Four tools (``docs/architecture/mcp-plugin.md`` §f):

* ``lemma_pod_start_workflow`` -- starts one of the workflows the person may
  run. A workflow takes no input when it starts: one that begins with a form
  starts waiting on it, and the form is its input.
* ``lemma_pod_submit_workflow_form`` -- fills in the form a run waits on, for a
  run the person started. A form waiting on them in someone else's run is that
  run's owner waiting for their decision; it is answered in Lemma, with the
  run in view, as an agent's approval is.
* ``lemma_pod_workflow_run`` -- where a run stands.
* ``lemma_pod_waiting_on_me`` -- the forms waiting on the person, each with
  where to answer it.

Every run is the person's, started through the pod's own assistant: their
access, and the workflow module's own checks of assignee and schema.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol
from uuid import UUID

from mcp.types import CallToolResult, Tool, ToolAnnotations
from pydantic import BaseModel, Field, ValidationError

from app.core.authorization.context import Context
from app.core.authorization.scope import context_scope
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.services.mcp_content import tool_call_result
from app.modules.agent.tools.authority import tool_authorization_context
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.tool_payload_limits import bounded_tool_payload
from app.modules.mcp_access.contracts import McpPrincipal, Scope
from app.modules.workflow.contracts.person_runs import (
    PersonWorkflowRun,
    PodWorkflow,
    WorkflowRefused,
)

START_WORKFLOW = "lemma_pod_start_workflow"
SUBMIT_FORM = "lemma_pod_submit_workflow_form"
WORKFLOW_RUN = "lemma_pod_workflow_run"
WAITING_ON_ME = "lemma_pod_waiting_on_me"
WORKFLOW_TOOLS = frozenset({START_WORKFLOW, SUBMIT_FORM, WORKFLOW_RUN, WAITING_ON_ME})
#: The two that change things, refused to a connection that may only read.
WRITING_TOOLS = frozenset({START_WORKFLOW, SUBMIT_FORM})

#: The same ceiling as apps, agents and functions.
MAX_WORKFLOWS = 20
MAX_WAITING = 20
_FORM = "HUMAN"
#: The pod tools over MCP run in no conversation, and so do these.
_NO_CONVERSATION = UUID(int=0)

_ACTS = {
    "read_only_hint": False,
    # A workflow runs agents and functions, and nothing says what they do.
    "destructive_hint": True,
    "idempotent_hint": False,
    "open_world_hint": True,
}
_READS = {
    "read_only_hint": True,
    "destructive_hint": False,
    "idempotent_hint": True,
    "open_world_hint": False,
}


class StartArguments(BaseModel):
    workflow: str = Field(min_length=1)


class SubmitArguments(BaseModel):
    run_id: UUID
    inputs: dict[str, object] = Field(default_factory=dict)


class RunArguments(BaseModel):
    run_id: UUID


class WorkflowGateway(Protocol):
    """What the tools need of the workflow module, for one connection."""

    async def workflows(self, principal: McpPrincipal) -> list[PodWorkflow]: ...

    async def start(self, principal: McpPrincipal, name: str) -> PersonWorkflowRun: ...

    async def run(self, principal: McpPrincipal, run_id: UUID) -> PersonWorkflowRun: ...

    async def waiting(self, principal: McpPrincipal) -> list[PersonWorkflowRun]: ...

    async def submit(
        self, principal: McpPrincipal, *, run_id: UUID, node_id: str, inputs: JsonObject
    ) -> tuple[bool, str]: ...


def is_workflow_tool(name: str) -> bool:
    return name in WORKFLOW_TOOLS


def workflow_tools(
    workflows: Sequence[PodWorkflow],
    *,
    may_read: bool,
    may_change: bool,
    forms_waiting: bool = False,
) -> list[Tool]:
    """The reading tools on a connection allowed to read; starting and
    submitting only on one allowed to change things. Each scope gates its own
    tools, as every pod tool's does: a connection granted only events is
    offered none of them.

    The waiting list is offered wherever a form waits on the person, even with
    no workflow they can see: being the assignee is the grant, and a form on a
    restricted workflow is still theirs to find."""
    if not workflows and not forms_waiting:
        return []
    run_read = [
        Tool(
            name=WORKFLOW_RUN,
            title="Check on a workflow run",
            description=(
                "Where a workflow run stands: its status, and the form or step "
                "it is waiting on."
            ),
            input_schema=_run_id_schema(),
            annotations=ToolAnnotations(title="Check on a workflow run", **_READS),
        )
    ]
    reads = [
        *(run_read if workflows else []),
        Tool(
            name=WAITING_ON_ME,
            title="Forms waiting on me",
            description=(
                "The workflow forms in this pod waiting on the person, newest "
                "first, each with where to answer it."
            ),
            input_schema={"type": "object", "properties": {}},
            annotations=ToolAnnotations(title="Forms waiting on me", **_READS),
        ),
    ]
    tools = reads if may_read else []
    startable = [workflow for workflow in workflows if workflow.may_start]
    if not may_change or not startable:
        return tools
    listing = "\n".join(_listed(workflow) for workflow in startable[:MAX_WORKFLOWS])
    return [
        Tool(
            name=START_WORKFLOW,
            title="Start a workflow",
            description=(
                "Start one of this pod's workflows, as the person. A workflow "
                "takes no input when it starts: one that begins with a form "
                "returns WAITING with the form's fields, and "
                f"{SUBMIT_FORM} fills them in.\n\n"
                f"The workflows:\n{listing}"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "workflow": {
                        "type": "string",
                        "enum": [workflow.name for workflow in startable],
                    }
                },
                "required": ["workflow"],
            },
            annotations=ToolAnnotations(title="Start a workflow", **_ACTS),
        ),
        Tool(
            name=SUBMIT_FORM,
            title="Fill in a workflow's form",
            description=(
                "Submit the form a workflow run is waiting on, for a run the "
                "person started. `inputs` are the form's fields by name. A form "
                "waiting on them in someone else's run is answered in Lemma."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "string", "format": "uuid"},
                    "inputs": {"type": "object"},
                },
                "required": ["run_id", "inputs"],
            },
            annotations=ToolAnnotations(title="Fill in a workflow's form", **_ACTS),
        ),
        *tools,
    ]


def _listed(workflow: PodWorkflow) -> str:
    about = (workflow.description or "").strip().replace("\n", " ")[:200]
    return f"- {workflow.name}: {about}" if about else f"- {workflow.name}"


def _run_id_schema() -> JsonObject:
    return {
        "type": "object",
        "properties": {"run_id": {"type": "string", "format": "uuid"}},
        "required": ["run_id"],
    }


def submit_refusal(run: PersonWorkflowRun, *, user_id: UUID, where: str) -> str | None:
    """Why the host may not submit this run's form, or ``None`` when it may.

    ``where`` is where the person answers it in Lemma, written into the
    refusal: the model passes on what it is told, and a link it has to look up
    elsewhere is a link it may not pass on."""
    wait = run.waiting
    if wait is None or wait.kind != _FORM:
        return "That run is not waiting on a form."
    if not wait.on_this_person:
        return "That form is not waiting on you."
    if run.started_by != user_id:
        return (
            "That form is in a run someone else started. They are waiting on "
            f"your decision: give it in Lemma, at {where}"
        )
    return None


def run_payload(
    run: PersonWorkflowRun, *, url: str, user_id: UUID, where: str | None = None
) -> JsonObject:
    """``url`` is the link for the payload; ``where`` how to reach it, when the
    link alone does not say (a form answered from the pod's waiting list)."""
    payload: JsonObject = {
        "success": run.status != "FAILED",
        "run_id": str(run.id),
        "workflow": run.workflow,
        "status": run.status,
        "url": url,
    }
    if run.error:
        payload["error"] = run.error
    wait = run.waiting
    if wait is None:
        return payload
    payload["waiting"] = {
        "node_id": wait.node_id,
        "kind": wait.kind,
        "on_you": wait.on_this_person,
        **(
            {"form": bounded_tool_payload(wait.payload, what="form")}
            if wait.kind == _FORM
            else {}
        ),
    }
    refusal = (
        submit_refusal(run, user_id=user_id, where=where or url)
        if wait.kind == _FORM
        else None
    )
    if wait.kind == _FORM and refusal is None:
        payload["message"] = (
            f"It is waiting on a form for you. Fill in its fields and call "
            f"{SUBMIT_FORM} with this run_id."
        )
    elif wait.kind == _FORM:
        payload["message"] = refusal
    return payload


class PodWorkflows:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        gateway: WorkflowGateway | None = None,
        run_url: Callable[[UUID, UUID], str] | None = None,
        space_url: Callable[[UUID], str] | None = None,
    ) -> None:
        self._gateway = gateway or _WorkflowModule(uow_factory)
        self._run_url = run_url or _lemma_run_url
        self._space_url = space_url or _lemma_space_url

    async def tools(self, principal: McpPrincipal) -> list[Tool]:
        """The workflow tools this connection is offered."""
        may_read = Scope.READ in principal.scopes
        workflows = await self._gateway.workflows(principal)
        # Asked only when nothing else would offer the waiting list: a pod with
        # no visible workflow can still hold a form assigned to the person.
        forms_waiting = (
            may_read and not workflows and bool(await self._gateway.waiting(principal))
        )
        return workflow_tools(
            workflows,
            may_read=may_read,
            may_change=Scope.WRITE in principal.scopes,
            forms_waiting=forms_waiting,
        )

    async def call(
        self, name: str, arguments: JsonObject, *, principal: McpPrincipal
    ) -> CallToolResult:
        try:
            if name == WAITING_ON_ME:
                return await self._listing(principal)
            if name == WORKFLOW_RUN:
                request = RunArguments.model_validate(arguments)
                return self._answer(
                    await self._gateway.run(principal, request.run_id), principal
                )
            if name == START_WORKFLOW:
                start = StartArguments.model_validate(arguments)
                return self._answer(
                    await self._gateway.start(principal, start.workflow), principal
                )
            return await self._submit(
                SubmitArguments.model_validate(arguments), principal
            )
        except ValidationError as exc:
            problem = exc.errors()[0]
            where = ".".join(str(part) for part in problem.get("loc", ()))
            return _refusal(f"{where or 'arguments'}: {problem.get('msg')}")
        except WorkflowRefused as exc:
            return _refusal(exc.message)

    async def _submit(
        self, request: SubmitArguments, principal: McpPrincipal
    ) -> CallToolResult:
        run = await self._gateway.run(principal, request.run_id)
        refusal = submit_refusal(
            run, user_id=principal.user_id, where=self._url(principal, run)
        )
        if refusal is not None or run.waiting is None:
            return _refusal(refusal or "That run is not waiting on a form.")
        submitted, message = await self._gateway.submit(
            principal,
            run_id=run.id,
            node_id=run.waiting.node_id,
            inputs=dict(request.inputs),
        )
        if not submitted:
            return _refusal(message)
        return self._answer(await self._gateway.run(principal, run.id), principal)

    def _answer(
        self, run: PersonWorkflowRun, principal: McpPrincipal
    ) -> CallToolResult:
        payload = run_payload(
            run, url=self._url(principal, run), user_id=principal.user_id
        )
        return tool_call_result(payload)

    async def _listing(self, principal: McpPrincipal) -> CallToolResult:
        runs = await self._gateway.waiting(principal)
        readable = {
            workflow.name for workflow in await self._gateway.workflows(principal)
        }
        return tool_call_result(
            {
                "success": True,
                "waiting": [
                    self._waiting_payload(run, principal, readable=readable)
                    for run in runs
                ],
            }
        )

    def _waiting_payload(
        self, run: PersonWorkflowRun, principal: McpPrincipal, *, readable: set[str]
    ) -> JsonObject:
        """A form waiting on the person, with where to answer it.

        A run's own page reads the run, which needs its workflow; a form
        assigned on a workflow the person may not read is answered from the
        pod's waiting list instead, so it is linked to the pod and says so.
        """
        if run.workflow in readable:
            return run_payload(
                run, url=self._url(principal, run), user_id=principal.user_id
            )
        space = self._space_url(principal.pod_id)
        return run_payload(
            run,
            url=space,
            user_id=principal.user_id,
            where=f"{space} , from the list of what is waiting on you",
        )

    def _url(self, principal: McpPrincipal, run: PersonWorkflowRun) -> str:
        return self._run_url(principal.pod_id, run.id)


class _WorkflowModule:
    """The gateway over the workflow module's contracts, as the person through
    the pod's own assistant -- the standing every pod tool has."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self.uow_factory = uow_factory

    async def workflows(self, principal: McpPrincipal) -> list[PodWorkflow]:
        from app.modules.workflow.contracts.person_runs import pod_workflows

        async with self.uow_factory() as uow:
            ctx = await _assistant_context(uow, principal)
            return await pod_workflows(
                uow, pod_id=principal.pod_id, ctx=ctx, limit=MAX_WORKFLOWS * 5
            )

    async def start(self, principal: McpPrincipal, name: str) -> PersonWorkflowRun:
        from app.modules.workflow.contracts.person_runs import start_workflow_run

        async with self.uow_factory() as uow:
            ctx = await _assistant_context(uow, principal)
            async with context_scope(ctx):
                run = await start_workflow_run(
                    uow,
                    pod_id=principal.pod_id,
                    name=name,
                    user_id=principal.user_id,
                    ctx=ctx,
                )
            await uow.commit()
        return run

    async def run(self, principal: McpPrincipal, run_id: UUID) -> PersonWorkflowRun:
        from app.modules.workflow.contracts.person_runs import workflow_run

        async with self.uow_factory() as uow:
            ctx = await _assistant_context(uow, principal)
            return await workflow_run(
                uow,
                pod_id=principal.pod_id,
                run_id=run_id,
                user_id=principal.user_id,
                ctx=ctx,
            )

    async def waiting(self, principal: McpPrincipal) -> list[PersonWorkflowRun]:
        from app.modules.workflow.contracts.person_runs import waiting_on

        async with self.uow_factory() as uow:
            return await waiting_on(
                uow,
                pod_id=principal.pod_id,
                user_id=principal.user_id,
                limit=MAX_WAITING,
            )

    async def submit(
        self, principal: McpPrincipal, *, run_id: UUID, node_id: str, inputs: JsonObject
    ) -> tuple[bool, str]:
        from app.modules.workflow.contracts.forms import submit_workflow_form

        return await submit_workflow_form(
            run_id=run_id,
            node_id=node_id,
            inputs=inputs,
            requester_user_id=principal.user_id,
        )


async def _assistant_context(
    uow: SqlAlchemyUnitOfWork, principal: McpPrincipal
) -> Context:
    """The standing every pod tool has over MCP: the person, through the pod's
    own assistant, in no conversation."""
    return await tool_authorization_context(
        uow,
        BaseAgentContext(
            user_id=principal.user_id,
            pod_id=principal.pod_id,
            conversation_id=_NO_CONVERSATION,
            is_pod_default_agent=True,
        ),
    )


def _lemma_run_url(pod_id: UUID, run_id: UUID) -> str:
    from app.modules.agent_surfaces.contracts.egress import workflow_run_url

    return workflow_run_url(pod_id=pod_id, run_id=run_id)


def _lemma_space_url(pod_id: UUID) -> str:
    from app.modules.agent_surfaces.contracts.egress import space_url

    return space_url(pod_id=pod_id)


def _refusal(message: str) -> CallToolResult:
    return tool_call_result({"success": False, "error": message})
