import asyncio
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from starlette import status
from sqlalchemy import select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.events import AgentRunCompletedEvent
from app.modules.agent.domain.value_objects import AgentRunStatus, ConversationStatus
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.function.domain.entities import FunctionRunStatus
from app.modules.function.domain.events import (
    FunctionRunCompletedEvent,
    FunctionRunFailedEvent,
)
from app.modules.function.infrastructure.repositories import FunctionRunRepository
from app.modules.pod.infrastructure.models.pod_models import PodMember
from app.modules.test_support.e2e_authz import (
    create_role_visibility_context,
    item_names,
)
from app.modules.workflow.domain.context import TriggerContext
from app.modules.workflow.domain.start import FlowStartType
from app.modules.workflow.events import handlers as wf_handlers
from app.modules.workflow.execution.engine import WorkflowEngine
from app.modules.workflow.services.run_resume_service import RunResumeService
from app.modules.workflow.services.schedule_start_service import ScheduleStartService

pytestmark = [pytest.mark.e2e, pytest.mark.workspace]


async def _create_pod(client: AsyncClient, org_id: str, name: str) -> str:
    response = await client.post(
        "/pods",
        json={
            "name": f"{name} {uuid4().hex[:6]}",
            "description": "Workflow E2E pod",
            "organization_id": org_id,
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _signup_user(async_client: AsyncClient, index: int) -> dict:
    email = f"test+{index}@example.com"
    password = "TestPassword@123"
    response = await async_client.post(
        "/st/auth/signup",
        json={
            "formFields": [
                {"id": "email", "value": email},
                {"id": "password", "value": password},
            ]
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    token = response.headers.get("st-access-token") or response.cookies.get(
        "sAccessToken"
    )
    assert token
    return {"email": email, "token": token, "user_id": data["user"]["id"]}


async def _add_reviewer_to_pod(
    owner_client: AsyncClient,
    async_client: AsyncClient,
    *,
    org_id: str,
    pod_id: str,
    index: int,
) -> dict:
    reviewer = await _signup_user(async_client, index)
    invitation = await owner_client.post(
        f"/organizations/{org_id}/invitations",
        json={"email": reviewer["email"], "role": "ORG_MEMBER"},
    )
    assert invitation.status_code == 201, invitation.text

    accept = await async_client.post(
        f"/organizations/invitations/{invitation.json()['id']}/accept",
        headers={"Authorization": f"Bearer {reviewer['token']}"},
    )
    assert accept.status_code == 200, accept.text

    members = await owner_client.get(f"/organizations/{org_id}/members")
    assert members.status_code == 200, members.text
    org_member = next(
        item
        for item in members.json()["items"]
        if item.get("user", {}).get("email") == reviewer["email"]
    )
    add = await owner_client.post(
        f"/pods/{pod_id}/members",
        json={"organization_member_id": org_member["id"], "roles": ["POD_EDITOR"]},
    )
    assert add.status_code == 201, add.text
    reviewer["organization_member_id"] = org_member["id"]
    reviewer["user_id"] = org_member["user"]["id"]
    return reviewer


async def _pod_member_id(
    db_session, *, pod_id: str, organization_member_id: str
) -> str:
    result = await db_session.execute(
        select(PodMember.id).where(
            PodMember.pod_id == UUID(pod_id),
            PodMember.organization_member_id == UUID(organization_member_id),
        )
    )
    member_id = result.scalar_one()
    return str(member_id)


async def _create_agent(client: AsyncClient, pod_id: str) -> str:
    response = await client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": f"review_parser_{uuid4().hex[:6]}",
            "description": "Parses reviewer payloads for workflow e2e",
            "instruction": "Return the provided line items as structured JSON.",
            "input_schema": {"type": "object"},
            "output_schema": {
                "type": "object",
                "properties": {"items": {"type": "array"}},
                "required": ["items"],
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["name"]


async def _create_echo_function(
    client: AsyncClient,
    pod_id: str,
    *,
    function_type: str = "API",
) -> str:
    function_name = f"record_line_{uuid4().hex[:6]}"
    code = f"""#input_type_name: InputModel
#output_type_name: OutputModel
#function_name: {function_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext


class InputModel(BaseModel):
    merchant: str
    amount: float
    kind: str


class OutputModel(BaseModel):
    merchant: str
    amount: float
    kind: str
    recorded: bool


async def {function_name}(ctx: FunctionContext, data: InputModel) -> OutputModel:
    return OutputModel(
        merchant=data.merchant,
        amount=data.amount,
        kind=data.kind,
        recorded=True,
    )
"""
    response = await client.post(
        f"/pods/{pod_id}/functions",
        json={
            "name": function_name,
            "description": "Echoes workflow line items",
            "type": function_type,
            "code": code,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["name"]


async def _create_workflow(
    client: AsyncClient,
    pod_id: str,
    *,
    name: str,
    start: dict,
    nodes: list[dict],
    edges: list[dict],
    mode: str = "GLOBAL",
) -> dict:
    create = await client.post(
        f"/pods/{pod_id}/workflows",
        json={"name": name, "start": start, "mode": mode},
    )
    assert create.status_code == 201, create.text
    workflow_name = create.json()["name"]

    graph = await client.put(
        f"/pods/{pod_id}/workflows/{workflow_name}/graph",
        json={"start": start, "nodes": nodes, "edges": edges},
    )
    assert graph.status_code == 200, graph.text
    return graph.json()


async def _create_run(
    client: AsyncClient,
    pod_id: str,
    workflow_name: str,
    *,
    headers: dict | None = None,
    expected_status: int = 201,
) -> dict:
    response = await client.post(
        f"/pods/{pod_id}/workflows/{workflow_name}/runs",
        headers=headers,
    )
    assert response.status_code == expected_status, response.text
    return response.json() if response.content else {}


async def _submit_form(
    client: AsyncClient,
    pod_id: str,
    run_id: str,
    *,
    node_id: str,
    inputs: dict,
    headers: dict | None = None,
    expected_status: int = 200,
) -> dict:
    response = await client.post(
        f"/pods/{pod_id}/workflow-runs/{run_id}/form",
        json={"node_id": node_id, "inputs": inputs},
        headers=headers,
    )
    assert response.status_code == expected_status, response.text
    return response.json() if response.content else {}


async def _get_run(client: AsyncClient, pod_id: str, run_id: str) -> dict:
    response = await client.get(f"/pods/{pod_id}/workflow-runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _wait_for_run(
    client: AsyncClient, pod_id: str, run_id: str, predicate, label: str
) -> dict:
    deadline = asyncio.get_running_loop().time() + 40
    while asyncio.get_running_loop().time() < deadline:
        run = await _get_run(client, pod_id, run_id)
        if run["status"] == "FAILED":
            pytest.fail(f"Workflow failed while waiting for {label}: {run}")
        if predicate(run):
            return run
        await asyncio.sleep(0.25)
    pytest.fail(f"Timed out waiting for {label}")


class _FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _InlineResumeJobQueue:
    """Stands in for the streaq worker: runs the enqueued workflow-resume jobs
    inline through the real RunResumeService so the
    event -> workflow handler -> resume -> engine -> stepper chain executes
    deterministically in-process (without the subprocess worker or a sandbox).
    """

    def __init__(self):
        self.enqueued: list[tuple[str, dict]] = []

    async def enqueue(self, job_name: str, **kwargs):
        self.enqueued.append((job_name, kwargs))
        async with create_uow_from_session_maker(async_session_maker) as uow:
            service = RunResumeService(WorkflowEngine(uow))
            if job_name == "resume_workflow_run_for_function":
                await service.resume_for_function_run(
                    function_run_id=kwargs["function_run_id"],
                    run_status=kwargs["run_status"],
                    output=kwargs.get("output"),
                )
            elif job_name == "resume_workflow_run_for_agent":
                await service.resume_for_agent_conversation(
                    conversation_id=kwargs["agent_conversation_id"],
                )
        return SimpleNamespace(id=kwargs.get("_job_id"))


async def _function_id_for_run(function_run_id: str) -> UUID:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        run = await FunctionRunRepository(uow).get_run(UUID(function_run_id))
        assert run is not None
        return run.function_id


async def _set_function_run_terminal(
    function_run_id: str,
    *,
    status: FunctionRunStatus,
    output_data: dict | None = None,
    error: str | None = None,
) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await FunctionRunRepository(uow).update_run(
            UUID(function_run_id),
            status=status,
            output_data=output_data,
            error=error,
            completed_at=datetime.now(),
        )
        await uow.commit()


async def _drive_function_completed(function_run_id: str, output_data: dict) -> None:
    """Mark the function run COMPLETED and drive the REAL workflow function
    event handler, exactly as the function worker + workflow subscriber would."""
    function_id = await _function_id_for_run(function_run_id)
    await _set_function_run_terminal(
        function_run_id, status=FunctionRunStatus.COMPLETED, output_data=output_data
    )
    event = FunctionRunCompletedEvent(
        run_id=UUID(function_run_id),
        function_id=function_id,
        output_data=output_data,
        completed_at=datetime.now(),
    ).model_dump(mode="json")
    await wf_handlers.handle_function_run_event(
        event, _FakeLogger(), job_queue=_InlineResumeJobQueue()
    )


async def _drive_function_failed(function_run_id: str, error: str) -> None:
    function_id = await _function_id_for_run(function_run_id)
    await _set_function_run_terminal(
        function_run_id, status=FunctionRunStatus.FAILED, error=error
    )
    event = FunctionRunFailedEvent(
        run_id=UUID(function_run_id),
        function_id=function_id,
        error=error,
        completed_at=datetime.now(),
    ).model_dump(mode="json")
    await wf_handlers.handle_function_run_event(
        event, _FakeLogger(), job_queue=_InlineResumeJobQueue()
    )


async def _drive_agent_event(conversation_id: str, *, status: AgentRunStatus) -> None:
    """Drive the REAL workflow agent event handler. The handler only enqueues a
    resume when an active AGENT wait exists, so a late/stale event is a no-op."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        agent_run = await ConversationRepository(uow).get_latest_agent_run_for_conversation(
            UUID(conversation_id),
        )
        assert agent_run is not None
        agent_run_id = agent_run.id
    event = AgentRunCompletedEvent(
        conversation_id=UUID(conversation_id),
        agent_run_id=agent_run_id,
        status=status,
    ).model_dump(mode="json")
    await wf_handlers.handle_agent_run_event(
        event,
        _FakeLogger(),
        job_queue=_InlineResumeJobQueue(),
        uow_factory=wf_handlers.provide_uow_factory(),
    )


async def _complete_agent_conversation(conversation_id: str, output_data: dict) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        conversation_repo = ConversationRepository(uow)
        conversation = await conversation_repo.get_conversation(
            UUID(conversation_id),
        )
        assert conversation is not None
        conversation.status = ConversationStatus.COMPLETED
        conversation.output = output_data
        await conversation_repo.update_conversation(conversation)
        agent_run = await conversation_repo.get_latest_agent_run_for_conversation(
            UUID(conversation_id),
        )
        assert agent_run is not None
        await conversation_repo.finish_agent_run(
            agent_run_id=agent_run.id,
            status=AgentRunStatus.COMPLETED,
            output_data=output_data,
        )
        await uow.commit()
    await _drive_agent_event(conversation_id, status=AgentRunStatus.COMPLETED)


async def _fail_agent_conversation(conversation_id: str, error: str) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        conversation_repo = ConversationRepository(uow)
        agent_run = await conversation_repo.get_latest_agent_run_for_conversation(
            UUID(conversation_id),
        )
        assert agent_run is not None
        await conversation_repo.finish_agent_run(
            agent_run_id=agent_run.id,
            status=AgentRunStatus.FAILED,
            conversation_status=ConversationStatus.FAILED,
            error=error,
        )
        await uow.commit()
    await _drive_agent_event(conversation_id, status=AgentRunStatus.FAILED)


async def _fire_wake(run_id: str) -> None:
    """Mimic the scheduler firing a wait_until wake for a run."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await ScheduleStartService(WorkflowEngine(uow)).handle_schedule_fired(
            schedule_id=run_id,
            payload={
                "workflow_run_id": run_id,
                "source": "workflow_wait_until",
            },
            metadata={"source": "test"},
            schedule_event_id=f"test:{uuid4()}",
        )


async def _create_simple_workflow(
    client: AsyncClient,
    pod_id: str,
    name: str,
    *,
    visibility: str | None = None,
) -> dict:
    payload: dict = {"name": name}
    if visibility is not None:
        payload["visibility"] = visibility
    response = await client.post(f"/pods/{pod_id}/workflows", json=payload)
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


@pytest.mark.asyncio
async def test_create_workflow_rejects_duplicate_name_in_same_pod(
    authenticated_client: AsyncClient,
    fixed_test_org,
):
    pod_id = await _create_pod(
        authenticated_client,
        fixed_test_org["id"],
        "workflow-duplicate",
    )
    workflow_name = f"duplicate workflow {uuid4().hex[:8]}"

    first = await authenticated_client.post(
        f"/pods/{pod_id}/workflows",
        json={"name": workflow_name},
    )
    assert first.status_code == status.HTTP_201_CREATED, first.text

    second = await authenticated_client.post(
        f"/pods/{pod_id}/workflows",
        json={"name": workflow_name},
    )
    assert second.status_code == status.HTTP_409_CONFLICT, second.text
    assert second.json()["code"] == "WORKFLOW_CONFLICT"


@pytest.mark.asyncio
async def test_invalid_graph_rejected_at_save_time(
    authenticated_client: AsyncClient,
    fixed_test_org,
):
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "workflow-invalid-graph"
    )
    create = await authenticated_client.post(
        f"/pods/{pod_id}/workflows",
        json={"name": f"invalid-{uuid4().hex[:6]}"},
    )
    assert create.status_code == 201, create.text
    name = create.json()["name"]

    graph = await authenticated_client.put(
        f"/pods/{pod_id}/workflows/{name}/graph",
        json={
            "nodes": [
                {"id": "a", "type": "FUNCTION", "config": {"function_name": "fn"}},
                {"id": "end", "type": "END"},
            ],
            "edges": [{"id": "e1", "source": "a", "target": "missing"}],
        },
    )
    assert graph.status_code == 422, graph.text
    assert graph.json()["code"] == "WORKFLOW_GRAPH_INVALID"
    assert "missing" in graph.json()["message"]


def _assigned_workflow_graph(
    *,
    first_reviewer_member_id: str,
    second_reviewer_member_id: str,
    agent_name: str,
    function_name: str,
) -> tuple[list[dict], list[dict]]:
    nodes = [
        {
            "id": "intake",
            "type": "FORM",
            "label": "Reviewer A intake",
            "config": {
                "assignee_pod_member_id": first_reviewer_member_id,
                "input_schema": {
                    "type": "object",
                    "properties": {"raw": {"type": "string"}},
                    "required": ["raw"],
                },
            },
        },
        {
            "id": "parse",
            "type": "AGENT",
            "label": "Parse input",
            "config": {
                "agent_name": agent_name,
                "input_mapping": {
                    "raw": {"type": "expression", "value": "intake.raw"},
                },
            },
        },
        {
            "id": "approval",
            "type": "FORM",
            "label": "Reviewer B approval",
            "config": {
                "assignee_pod_member_id": second_reviewer_member_id,
                "input_schema": {
                    "type": "object",
                    "properties": {"approved": {"type": "boolean"}},
                    "required": ["approved"],
                },
            },
        },
        {
            "id": "approved_route",
            "type": "DECISION",
            "label": "Approved?",
            "config": {
                "rules": [
                    {
                        "condition": "approval.approved == `true`",
                        "next_node_id": "line_loop",
                    },
                ]
            },
        },
        {
            "id": "line_loop",
            "type": "LOOP",
            "label": "Each line item",
            "config": {
                "items_path": "parse.items",
                "item_var_name": "line",
                "child_node_id": "record_line",
            },
        },
        {
            "id": "record_line",
            "type": "FUNCTION",
            "label": "Record item",
            "config": {
                "function_name": function_name,
                "input_mapping": {
                    "merchant": {"type": "expression", "value": "loop.line.merchant"},
                    "amount": {"type": "expression", "value": "loop.line.amount"},
                    "kind": {"type": "expression", "value": "loop.line.kind"},
                },
            },
        },
        {
            "id": "cooldown",
            "type": "WAIT_UNTIL",
            "label": "Cooldown",
            "config": {"timeout_seconds": 60},
        },
        {"id": "end", "type": "END", "label": "Done"},
    ]
    edges = [
        {"id": "e1", "source": "intake", "target": "parse"},
        {"id": "e2", "source": "parse", "target": "approval"},
        {"id": "e3", "source": "approval", "target": "approved_route"},
        {"id": "e4", "source": "approved_route", "target": "end"},
        {"id": "e5", "source": "line_loop", "target": "cooldown"},
        {"id": "e6", "source": "record_line", "target": "line_loop"},
        {"id": "e7", "source": "cooldown", "target": "end"},
    ]
    return nodes, edges


async def _assigned_waits(
    client: AsyncClient,
    pod_id: str,
    token: str,
) -> list[dict]:
    response = await client.get(
        f"/pods/{pod_id}/workflow-runs/waiting/assigned-to-me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


@pytest.mark.asyncio
async def test_user_assigned_manual_workflow_runs_through_all_node_types(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    db_session,
    configure_workspace_api_url,
):
    _ = configure_workspace_api_url
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "Workflow Assignments"
    )
    reviewer_a = await _add_reviewer_to_pod(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        index=0,
    )
    reviewer_b = await _add_reviewer_to_pod(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        index=1,
    )
    reviewer_a_member_id = await _pod_member_id(
        db_session,
        pod_id=pod_id,
        organization_member_id=reviewer_a["organization_member_id"],
    )
    reviewer_b_member_id = await _pod_member_id(
        db_session,
        pod_id=pod_id,
        organization_member_id=reviewer_b["organization_member_id"],
    )
    agent_name = await _create_agent(authenticated_client, pod_id)
    function_name = await _create_echo_function(authenticated_client, pod_id)
    nodes, edges = _assigned_workflow_graph(
        first_reviewer_member_id=reviewer_a_member_id,
        second_reviewer_member_id=reviewer_b_member_id,
        agent_name=agent_name,
        function_name=function_name,
    )
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        name=f"assigned-review-{uuid4().hex[:6]}",
        start={"type": "MANUAL"},
        nodes=nodes,
        edges=edges,
    )

    # Manual create-run takes no body; the run is immediately WAITING on the
    # entry form, with the wait (incl. form schema) in the response.
    run = await _create_run(authenticated_client, pod_id, workflow["name"])
    assert run["status"] == "WAITING"
    assert run["current_node_id"] == "intake"
    assert run["start_type"] == "MANUAL"
    assert "start" not in run["execution_context"]
    wait = run["active_wait"]
    assert wait is not None and wait["wait_type"] == "HUMAN"
    assert wait["node_id"] == "intake"
    assert wait["payload"]["input_schema"]["properties"]["raw"]["type"] == "string"
    assert wait["assigned_pod_member_id"] == reviewer_a_member_id
    assert wait["created_at"]

    assert [
        item["run"]["id"]
        for item in await _assigned_waits(async_client, pod_id, reviewer_a["token"])
    ] == [run["id"]]
    assert await _assigned_waits(async_client, pod_id, reviewer_b["token"]) == []

    # Wrong assignee -> 403
    await _submit_form(
        async_client,
        pod_id,
        run["id"],
        node_id="intake",
        inputs={"raw": "Uber 23.50 and Payroll 2500"},
        headers={"Authorization": f"Bearer {reviewer_b['token']}"},
        expected_status=403,
    )
    # Wrong node -> 422
    await _submit_form(
        async_client,
        pod_id,
        run["id"],
        node_id="approval",
        inputs={"approved": True},
        headers={"Authorization": f"Bearer {reviewer_a['token']}"},
        expected_status=422,
    )

    run = await _submit_form(
        async_client,
        pod_id,
        run["id"],
        node_id="intake",
        inputs={"raw": "Uber 23.50 and Payroll 2500"},
        headers={"Authorization": f"Bearer {reviewer_a['token']}"},
    )
    assert run["status"] == "RUNNING"
    assert run["current_node_id"] == "parse"
    assert run["active_wait"]["wait_type"] == "AGENT"
    conversation_id = run["active_wait"]["external_ref"]
    assert conversation_id

    # Double submit while the run is on a non-human agent wait -> 409.
    await _submit_form(
        async_client,
        pod_id,
        run["id"],
        node_id="intake",
        inputs={"raw": "again"},
        headers={"Authorization": f"Bearer {reviewer_a['token']}"},
        expected_status=409,
    )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        messages, _ = await ConversationRepository(uow).list_messages(
            conversation_id=UUID(conversation_id),
        )
    assert len(messages) == 1
    agent_prompt = messages[0].text or ""
    assert '"raw": "Uber 23.50 and Payroll 2500"' in agent_prompt

    agent_output = {
        "items": [
            {"merchant": "Uber", "amount": 23.5, "kind": "expense"},
            {"merchant": "Payroll", "amount": 2500.0, "kind": "income"},
        ]
    }
    await _complete_agent_conversation(conversation_id, agent_output)

    run = await _wait_for_run(
        authenticated_client,
        pod_id,
        run["id"],
        lambda current: (
            current["status"] == "WAITING" and current["current_node_id"] == "approval"
        ),
        "second reviewer form wait",
    )
    assert [
        item["run"]["id"]
        for item in await _assigned_waits(async_client, pod_id, reviewer_b["token"])
    ] == [run["id"]]

    run = await _submit_form(
        async_client,
        pod_id,
        run["id"],
        node_id="approval",
        inputs={"approved": True},
        headers={"Authorization": f"Bearer {reviewer_b['token']}"},
    )
    assert run["status"] == "RUNNING"
    assert run["current_node_id"] == "cooldown"
    assert run["active_wait"]["wait_type"] == "TIME"
    timer_id = run["active_wait"]["external_ref"]
    assert timer_id == run["id"]

    await _fire_wake(run["id"])
    completed = await _wait_for_run(
        authenticated_client,
        pod_id,
        run["id"],
        lambda current: current["status"] == "COMPLETED",
        "workflow completion after wait schedule",
    )
    history = [step["node_id"] for step in completed["step_history"]]
    for expected in [
        "intake",
        "parse",
        "approval",
        "approved_route",
        "line_loop",
        "record_line",
        "cooldown",
        "end",
    ]:
        assert expected in history
    context = completed["execution_context"]
    # Same flat shape regardless of which surface submitted the forms.
    assert context["intake"] == {"raw": "Uber 23.50 and Payroll 2500"}
    assert context["approval"] == {"approved": True}
    assert context["parse"] == agent_output
    assert context["line_loop"] == {
        "results": [
            {
                "merchant": "Uber",
                "amount": 23.5,
                "kind": "expense",
                "recorded": True,
            },
            {
                "merchant": "Payroll",
                "amount": 2500.0,
                "kind": "income",
                "recorded": True,
            },
        ],
        "count": 2,
    }


@pytest.mark.asyncio
async def test_non_form_manual_workflow_runs_immediately(
    authenticated_client: AsyncClient,
    fixed_test_org,
):
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "Workflow Direct"
    )
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        name=f"direct-{uuid4().hex[:6]}",
        start={"type": "MANUAL"},
        nodes=[{"id": "end", "type": "END", "label": "Done"}],
        edges=[],
    )

    run = await _create_run(authenticated_client, pod_id, workflow["name"])
    assert run["status"] == "COMPLETED"
    assert run["start_type"] == "MANUAL"
    assert run["active_wait"] is None
    assert "start" not in run["execution_context"]

    list_response = await authenticated_client.get(
        f"/pods/{pod_id}/workflows/{workflow['name']}/runs",
    )
    assert list_response.status_code == 200, list_response.text
    listed_run = list_response.json()["items"][0]
    assert listed_run["id"] == run["id"]
    assert "execution_context" not in listed_run
    assert "step_history" not in listed_run


@pytest.mark.asyncio
async def test_scheduled_single_api_function_workflow_completes_inline(
    authenticated_client: AsyncClient,
    fixed_test_org,
    configure_workspace_api_url,
):
    _ = configure_workspace_api_url
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "Workflow Scheduled API"
    )
    function_name = await _create_echo_function(authenticated_client, pod_id)
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        name=f"scheduled-api-{uuid4().hex[:6]}",
        start={"type": "SCHEDULED", "config": {"schedule_type": "ONCE"}},
        nodes=[
            {
                "id": "record",
                "type": "FUNCTION",
                "config": {
                    "function_name": function_name,
                    "input_mapping": {
                        "merchant": {
                            "type": "expression",
                            "value": "start.payload.merchant",
                        },
                        "amount": {
                            "type": "expression",
                            "value": "start.payload.amount",
                        },
                        "kind": {"type": "literal", "value": "expense"},
                    },
                },
            },
            {"id": "end", "type": "END"},
        ],
        edges=[{"id": "e1", "source": "record", "target": "end"}],
    )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        engine = WorkflowEngine(uow)
        flow = await engine.flow_repo.get_by_name(UUID(pod_id), workflow["name"])
        assert flow is not None
        assert flow.user_id is not None
        run = await engine.start_run(
            flow.id,
            flow.user_id,
            trigger=TriggerContext(
                trigger_type=FlowStartType.SCHEDULED,
                payload={"merchant": "Uber", "amount": 23.5},
                metadata={"schedule_type": "TIME"},
            ),
            schedule_event_id=f"test:{uuid4()}",
        )

    fetched = await _get_run(authenticated_client, pod_id, str(run.id))
    assert fetched["status"] == "COMPLETED"
    assert fetched["active_wait"] is None
    assert fetched["execution_context"]["record"] == {
        "merchant": "Uber",
        "amount": 23.5,
        "kind": "expense",
        "recorded": True,
    }


@pytest.mark.asyncio
async def test_single_job_function_workflow_runs_with_function_wait_not_waiting(
    authenticated_client: AsyncClient,
    fixed_test_org,
    configure_workspace_api_url,
):
    _ = configure_workspace_api_url
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "Workflow Job Function"
    )
    function_name = await _create_echo_function(
        authenticated_client,
        pod_id,
        function_type="JOB",
    )
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        name=f"job-function-{uuid4().hex[:6]}",
        start={"type": "MANUAL"},
        nodes=[
            {
                "id": "record",
                "type": "FUNCTION",
                "config": {
                    "function_name": function_name,
                    "input_mapping": {
                        "merchant": {"type": "literal", "value": "Uber"},
                        "amount": {"type": "literal", "value": 23.5},
                        "kind": {"type": "literal", "value": "expense"},
                    },
                },
            },
            {"id": "end", "type": "END"},
        ],
        edges=[{"id": "e1", "source": "record", "target": "end"}],
    )

    run = await _create_run(authenticated_client, pod_id, workflow["name"])

    assert run["status"] == "RUNNING"
    assert run["current_node_id"] == "record"
    assert run["active_wait"]["wait_type"] == "FUNCTION"
    assert run["active_wait"]["external_ref"]


@pytest.mark.asyncio
async def test_job_function_failure_fails_workflow_run(
    authenticated_client: AsyncClient,
    fixed_test_org,
    configure_workspace_api_url,
):
    _ = configure_workspace_api_url
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "Workflow Job Failure"
    )
    function_name = await _create_echo_function(
        authenticated_client,
        pod_id,
        function_type="JOB",
    )
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        name=f"job-fail-{uuid4().hex[:6]}",
        start={"type": "MANUAL"},
        nodes=[
            {
                "id": "record",
                "type": "FUNCTION",
                "config": {
                    "function_name": function_name,
                    "input_mapping": {
                        "merchant": {"type": "literal", "value": "Uber"},
                        "amount": {"type": "literal", "value": 23.5},
                        "kind": {"type": "literal", "value": "expense"},
                    },
                },
            },
            {"id": "end", "type": "END"},
        ],
        edges=[{"id": "e1", "source": "record", "target": "end"}],
    )

    run = await _create_run(authenticated_client, pod_id, workflow["name"])
    function_run_id = run["active_wait"]["external_ref"]

    # Drive the REAL FunctionRunFailedEvent -> workflow handler -> resume chain.
    await _drive_function_failed(function_run_id, "Function exploded")

    failed = await _get_run(authenticated_client, pod_id, run["id"])
    assert failed["status"] == "FAILED"
    assert failed["failed_node_id"] == "record"
    assert failed["error"] == "Function exploded"
    assert failed["active_wait"] is None
    failed_steps = [s for s in failed["step_history"] if s["status"] == "FAILED"]
    assert failed_steps and failed_steps[0]["node_id"] == "record"


@pytest.mark.asyncio
async def test_job_function_completion_resumes_run_via_event(
    authenticated_client: AsyncClient,
    fixed_test_org,
    configure_workspace_api_url,
):
    _ = configure_workspace_api_url
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "Workflow Job Complete"
    )
    function_name = await _create_echo_function(
        authenticated_client, pod_id, function_type="JOB"
    )
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        name=f"job-complete-{uuid4().hex[:6]}",
        start={"type": "MANUAL"},
        nodes=[
            {
                "id": "record",
                "type": "FUNCTION",
                "config": {
                    "function_name": function_name,
                    "input_mapping": {
                        "merchant": {"type": "literal", "value": "Uber"},
                        "amount": {"type": "literal", "value": 23.5},
                        "kind": {"type": "literal", "value": "expense"},
                    },
                },
            },
            {"id": "end", "type": "END"},
        ],
        edges=[{"id": "e1", "source": "record", "target": "end"}],
    )

    run = await _create_run(authenticated_client, pod_id, workflow["name"])
    assert run["status"] == "RUNNING"
    assert run["active_wait"]["wait_type"] == "FUNCTION"
    function_run_id = run["active_wait"]["external_ref"]

    output = {"merchant": "Uber", "amount": 23.5, "kind": "expense", "recorded": True}
    await _drive_function_completed(function_run_id, output)

    completed = await _wait_for_run(
        authenticated_client,
        pod_id,
        run["id"],
        lambda r: r["status"] == "COMPLETED",
        "job function completion",
    )
    assert completed["active_wait"] is None
    assert completed["execution_context"]["record"] == output


@pytest.mark.asyncio
async def test_duplicate_function_completion_event_is_idempotent(
    authenticated_client: AsyncClient,
    fixed_test_org,
    configure_workspace_api_url,
):
    _ = configure_workspace_api_url
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "Workflow Job Dup"
    )
    function_name = await _create_echo_function(
        authenticated_client, pod_id, function_type="JOB"
    )
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        name=f"job-dup-{uuid4().hex[:6]}",
        start={"type": "MANUAL"},
        nodes=[
            {
                "id": "record",
                "type": "FUNCTION",
                "config": {
                    "function_name": function_name,
                    "input_mapping": {
                        "merchant": {"type": "literal", "value": "Uber"},
                        "amount": {"type": "literal", "value": 23.5},
                        "kind": {"type": "literal", "value": "expense"},
                    },
                },
            },
            {"id": "end", "type": "END"},
        ],
        edges=[{"id": "e1", "source": "record", "target": "end"}],
    )

    run = await _create_run(authenticated_client, pod_id, workflow["name"])
    function_run_id = run["active_wait"]["external_ref"]
    output = {"merchant": "Uber", "amount": 23.5, "kind": "expense", "recorded": True}

    # The same completion event delivered twice must resume the run exactly once.
    await _drive_function_completed(function_run_id, output)
    await _drive_function_completed(function_run_id, output)

    completed = await _get_run(authenticated_client, pod_id, run["id"])
    assert completed["status"] == "COMPLETED"
    record_steps = [
        s
        for s in completed["step_history"]
        if s["node_id"] == "record" and s["status"] == "COMPLETED"
    ]
    assert len(record_steps) == 1


@pytest.mark.asyncio
async def test_agent_failure_fails_workflow_run(
    authenticated_client: AsyncClient,
    fixed_test_org,
    configure_workspace_api_url,
):
    _ = configure_workspace_api_url
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "Workflow Agent Failure"
    )
    agent_name = await _create_agent(authenticated_client, pod_id)
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        name=f"agent-fail-{uuid4().hex[:6]}",
        start={"type": "MANUAL"},
        nodes=[
            {
                "id": "parse",
                "type": "AGENT",
                "config": {"agent_name": agent_name, "input_mapping": {}},
            },
            {"id": "end", "type": "END"},
        ],
        edges=[{"id": "e1", "source": "parse", "target": "end"}],
    )

    run = await _create_run(authenticated_client, pod_id, workflow["name"])
    conversation_id = run["active_wait"]["external_ref"]

    await _fail_agent_conversation(conversation_id, "agent tool crashed")

    failed = await _get_run(authenticated_client, pod_id, run["id"])
    assert failed["status"] == "FAILED"
    assert failed["failed_node_id"] == "parse"
    assert failed["error"] == "Agent conversation FAILED"
    assert failed["active_wait"] is None
    failed_steps = [s for s in failed["step_history"] if s["status"] == "FAILED"]
    assert failed_steps and failed_steps[0]["node_id"] == "parse"


@pytest.mark.asyncio
async def test_triggered_run_reads_start_namespace_only(
    authenticated_client: AsyncClient,
    fixed_test_org,
):
    """Trigger payloads live at start.* and start_type reflects the trigger."""
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "Workflow Trigger"
    )
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        name=f"trigger-{uuid4().hex[:6]}",
        start={
            "type": "DATASTORE_EVENT",
            "config": {"table_name": "records", "operations": ["INSERT"]},
        },
        nodes=[{"id": "end", "type": "END", "label": "Done"}],
        edges=[],
    )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        engine = WorkflowEngine(uow)
        flow = await engine.flow_repo.get_by_name(UUID(pod_id), workflow["name"])
        run = await engine.start_run(
            flow.id,
            flow.user_id,
            trigger=TriggerContext(
                trigger_type=FlowStartType.DATASTORE_EVENT,
                payload={"record": {"id": "r1"}},
                metadata={"table_name": "records", "operation": "INSERT"},
            ),
            schedule_event_id=f"test:{uuid4()}",
        )

    fetched = await _get_run(authenticated_client, pod_id, str(run.id))
    assert fetched["status"] == "COMPLETED"
    assert fetched["start_type"] == "DATASTORE_EVENT"
    context = fetched["execution_context"]
    assert context["start"]["payload"] == {"record": {"id": "r1"}}
    assert context["start"]["metadata"]["table_name"] == "records"
    # No root-level merging.
    assert "record" not in context
    assert "payload" not in context


@pytest.mark.asyncio
async def test_run_on_graphless_workflow_rejected(
    authenticated_client: AsyncClient,
    fixed_test_org,
):
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "Workflow Empty Graph"
    )
    create = await authenticated_client.post(
        f"/pods/{pod_id}/workflows",
        json={
            "name": f"empty-{uuid4().hex[:6]}",
            "start": {"type": "MANUAL", "config": None},
        },
    )
    assert create.status_code == 201, create.text
    workflow_name = create.json()["name"]

    response = await authenticated_client.post(
        f"/pods/{pod_id}/workflows/{workflow_name}/runs",
    )
    assert response.status_code == 400, response.text
    assert "no graph" in response.json()["message"]


@pytest.mark.asyncio
async def test_failed_node_run_surfaces_error_and_failed_node_id(
    authenticated_client: AsyncClient,
    fixed_test_org,
    configure_workspace_api_url,
):
    """A node failure (missing agent) must name the failing node and its reason."""
    _ = configure_workspace_api_url
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "Workflow Node Failure"
    )
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        name=f"node-fail-{uuid4().hex[:6]}",
        start={"type": "MANUAL", "config": None},
        nodes=[
            {
                "id": "call_agent",
                "type": "AGENT",
                "label": "Call missing agent",
                "config": {"agent_name": "does-not-exist", "input_mapping": {}},
            },
            {"id": "end", "type": "END", "label": "Done"},
        ],
        edges=[{"id": "e1", "source": "call_agent", "target": "end"}],
    )

    run = await _create_run(authenticated_client, pod_id, workflow["name"])
    assert run["status"] == "FAILED"
    assert run["error"], run
    assert "does-not-exist" in run["error"] or "not found" in run["error"].lower()
    assert run["failed_node_id"] == "call_agent", run
    assert len(run["error"]) <= 2000, len(run["error"])

    fetched = await _get_run(authenticated_client, pod_id, run["id"])
    assert fetched["status"] == "FAILED"
    assert fetched["error"] == run["error"]
    assert fetched["failed_node_id"] == "call_agent"
    failed_steps = [s for s in fetched["step_history"] if s["status"] == "FAILED"]
    assert failed_steps and failed_steps[0]["node_id"] == "call_agent"
    assert failed_steps[0]["error"]

    listed = await authenticated_client.get(
        f"/pods/{pod_id}/workflows/{workflow['name']}/runs",
    )
    assert listed.status_code == 200, listed.text
    summary = listed.json()["items"][0]
    assert summary["id"] == run["id"]
    assert summary["error"] == run["error"]


@pytest.mark.asyncio
async def test_missing_context_path_fails_run_loudly(
    authenticated_client: AsyncClient,
    fixed_test_org,
    configure_workspace_api_url,
):
    _ = configure_workspace_api_url
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "Workflow Strict Paths"
    )
    function_name = await _create_echo_function(authenticated_client, pod_id)
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        name=f"strict-{uuid4().hex[:6]}",
        start={"type": "MANUAL"},
        nodes=[
            {
                "id": "intake",
                "type": "FORM",
                "config": {
                    "input_schema": {
                        "type": "object",
                        "properties": {"merchant": {"type": "string"}},
                    }
                },
            },
            {
                "id": "record",
                "type": "FUNCTION",
                "config": {
                    "function_name": function_name,
                    "input_mapping": {
                        # Typo'd path: resolves to nothing and must fail loudly.
                        "merchant": {"type": "expression", "value": "intake.merchantt"},
                        "amount": {"type": "literal", "value": 1.0},
                        "kind": {"type": "literal", "value": "expense"},
                    },
                },
            },
            {"id": "end", "type": "END"},
        ],
        edges=[
            {"id": "e1", "source": "intake", "target": "record"},
            {"id": "e2", "source": "record", "target": "end"},
        ],
    )

    run = await _create_run(authenticated_client, pod_id, workflow["name"])
    assert run["status"] == "WAITING"
    run = await _submit_form(
        authenticated_client,
        pod_id,
        run["id"],
        node_id="intake",
        inputs={"merchant": "Uber"},
    )
    assert run["status"] == "FAILED"
    assert run["failed_node_id"] == "record"
    assert "intake.merchantt" in run["error"]
    assert "merchant" in run["error"]


@pytest.mark.asyncio
async def test_form_submit_validates_against_schema_and_merges_defaults(
    authenticated_client: AsyncClient,
    fixed_test_org,
):
    """Submitting a form validates inputs against the resolved schema on the
    wait (dynamic enums enforced server-side) and fills omitted fields from
    schema defaults."""
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "Workflow Form Validation"
    )
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        name=f"formval-{uuid4().hex[:6]}",
        start={"type": "MANUAL", "config": None},
        nodes=[
            {
                "id": "intake",
                "type": "FORM",
                "config": {
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "enum": ["a", "b"]},
                            "note": {"type": "string", "default": "n/a"},
                        },
                        "required": ["category"],
                    }
                },
            },
            {"id": "end", "type": "END"},
        ],
        edges=[{"id": "e1", "source": "intake", "target": "end"}],
    )

    run = await _create_run(authenticated_client, pod_id, workflow["name"])
    assert run["status"] == "WAITING"

    # A value outside the enum is rejected; the run stays WAITING.
    await _submit_form(
        authenticated_client,
        pod_id,
        run["id"],
        node_id="intake",
        inputs={"category": "z"},
        expected_status=422,
    )
    still_waiting = await _get_run(authenticated_client, pod_id, run["id"])
    assert still_waiting["status"] == "WAITING"

    # A valid value with the optional field omitted: succeeds, default merged.
    run = await _submit_form(
        authenticated_client,
        pod_id,
        run["id"],
        node_id="intake",
        inputs={"category": "a"},
    )
    assert run["status"] == "COMPLETED"
    assert run["execution_context"]["intake"] == {"category": "a", "note": "n/a"}


@pytest.mark.asyncio
async def test_agent_without_output_schema_resumes_workflow_with_answer(
    authenticated_client: AsyncClient,
    fixed_test_org,
):
    """An agent with no output_schema completes with a bare string. The run
    must resume — the string is wrapped as {"answer": ...} — instead of hanging
    forever on the AGENT wait. Regression for dict(output) crashing on a
    non-dict completion output."""
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "Workflow No-Schema Agent"
    )
    agent_resp = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": f"acker_{uuid4().hex[:6]}",
            "description": "No output schema.",
            "instruction": "Acknowledge the workflow input in one short sentence.",
        },
    )
    assert agent_resp.status_code == 201, agent_resp.text
    agent_name = agent_resp.json()["name"]

    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        name=f"noschema-{uuid4().hex[:6]}",
        start={"type": "MANUAL", "config": None},
        nodes=[
            {
                "id": "ack",
                "type": "AGENT",
                "config": {"agent_name": agent_name, "input_mapping": {}},
            },
            {"id": "end", "type": "END"},
        ],
        edges=[{"id": "e1", "source": "ack", "target": "end"}],
    )

    run = await _create_run(authenticated_client, pod_id, workflow["name"])
    run = await _get_run(authenticated_client, pod_id, run["id"])
    assert run["active_wait"]["wait_type"] == "AGENT"
    conversation_id = run["active_wait"]["external_ref"]
    assert conversation_id

    # No output_schema -> the conversation completes with a BARE STRING.
    await _complete_agent_conversation(conversation_id, "All done here.")

    run = await _get_run(authenticated_client, pod_id, run["id"])
    assert run["status"] == "COMPLETED", run
    assert run["execution_context"]["ack"] == {"answer": "All done here."}


@pytest.mark.asyncio
async def test_cancel_waiting_run_and_drop_stale_completion(
    authenticated_client: AsyncClient,
    fixed_test_org,
    configure_workspace_api_url,
):
    _ = configure_workspace_api_url
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "Workflow Cancel"
    )
    agent_name = await _create_agent(authenticated_client, pod_id)
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        name=f"cancel-{uuid4().hex[:6]}",
        start={"type": "MANUAL"},
        nodes=[
            {
                "id": "parse",
                "type": "AGENT",
                "config": {"agent_name": agent_name, "input_mapping": {}},
            },
            {"id": "end", "type": "END"},
        ],
        edges=[{"id": "e1", "source": "parse", "target": "end"}],
    )

    run = await _create_run(authenticated_client, pod_id, workflow["name"])
    assert run["status"] == "RUNNING"
    conversation_id = run["active_wait"]["external_ref"]

    cancel = await authenticated_client.post(
        f"/pods/{pod_id}/workflow-runs/{run['id']}/cancel",
    )
    assert cancel.status_code == 200, cancel.text
    cancelled = cancel.json()
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["active_wait"] is None
    assert cancelled["completed_at"]

    # Cancelling again -> 409
    again = await authenticated_client.post(
        f"/pods/{pod_id}/workflow-runs/{run['id']}/cancel",
    )
    assert again.status_code == 409, again.text

    # A late agent completion is a no-op, not an error or a resurrection.
    await _complete_agent_conversation(conversation_id, {"late": True})
    fetched = await _get_run(authenticated_client, pod_id, run["id"])
    assert fetched["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_reconciliation_recovers_lost_agent_completion(
    authenticated_client: AsyncClient,
    fixed_test_org,
    configure_workspace_api_url,
):
    """If the completion event is lost, the sweep resumes the run."""
    _ = configure_workspace_api_url
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "Workflow Reconcile"
    )
    agent_name = await _create_agent(authenticated_client, pod_id)
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        name=f"reconcile-{uuid4().hex[:6]}",
        start={"type": "MANUAL"},
        nodes=[
            {
                "id": "parse",
                "type": "AGENT",
                "config": {"agent_name": agent_name, "input_mapping": {}},
            },
            {"id": "end", "type": "END"},
        ],
        edges=[{"id": "e1", "source": "parse", "target": "end"}],
    )

    run = await _create_run(authenticated_client, pod_id, workflow["name"])
    conversation_id = run["active_wait"]["external_ref"]

    # Complete the conversation WITHOUT notifying the workflow (lost event).
    async with create_uow_from_session_maker(async_session_maker) as uow:
        conversation_repo = ConversationRepository(uow)
        conversation = await conversation_repo.get_conversation(UUID(conversation_id))
        conversation.status = ConversationStatus.COMPLETED
        conversation.output = {"answer": 42}
        await conversation_repo.update_conversation(conversation)
        await uow.commit()

    # Sweep with a zero threshold so the fresh wait qualifies as stale.
    import app.modules.workflow.services.run_resume_service as rrs

    original = rrs.RECONCILE_AFTER
    rrs.RECONCILE_AFTER = rrs.timedelta(seconds=0)
    try:
        async with create_uow_from_session_maker(async_session_maker) as uow:
            acted = await RunResumeService(WorkflowEngine(uow)).reconcile_stale_waits()
    finally:
        rrs.RECONCILE_AFTER = original
    assert acted == 1

    fetched = await _get_run(authenticated_client, pod_id, run["id"])
    assert fetched["status"] == "COMPLETED"
    assert fetched["execution_context"]["parse"] == {"answer": 42}


@pytest.mark.asyncio
async def test_reconciliation_recovers_lost_function_completion(
    authenticated_client: AsyncClient,
    fixed_test_org,
    configure_workspace_api_url,
):
    """If a JOB function's completion event is lost, the sweep resumes the run."""
    _ = configure_workspace_api_url
    pod_id = await _create_pod(
        authenticated_client, fixed_test_org["id"], "Workflow Fn Reconcile"
    )
    function_name = await _create_echo_function(
        authenticated_client, pod_id, function_type="JOB"
    )
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        name=f"fn-reconcile-{uuid4().hex[:6]}",
        start={"type": "MANUAL"},
        nodes=[
            {
                "id": "record",
                "type": "FUNCTION",
                "config": {
                    "function_name": function_name,
                    "input_mapping": {
                        "merchant": {"type": "literal", "value": "Uber"},
                        "amount": {"type": "literal", "value": 23.5},
                        "kind": {"type": "literal", "value": "expense"},
                    },
                },
            },
            {"id": "end", "type": "END"},
        ],
        edges=[{"id": "e1", "source": "record", "target": "end"}],
    )

    run = await _create_run(authenticated_client, pod_id, workflow["name"])
    function_run_id = run["active_wait"]["external_ref"]
    output = {"merchant": "Uber", "amount": 23.5, "kind": "expense", "recorded": True}

    # Complete the function run WITHOUT notifying the workflow (lost event).
    await _set_function_run_terminal(
        function_run_id, status=FunctionRunStatus.COMPLETED, output_data=output
    )

    # Sweep with a zero threshold so the fresh wait qualifies as stale.
    import app.modules.workflow.services.run_resume_service as rrs

    original = rrs.RECONCILE_AFTER
    rrs.RECONCILE_AFTER = rrs.timedelta(seconds=0)
    try:
        async with create_uow_from_session_maker(async_session_maker) as uow:
            acted = await RunResumeService(WorkflowEngine(uow)).reconcile_stale_waits()
    finally:
        rrs.RECONCILE_AFTER = original
    assert acted == 1

    fetched = await _get_run(authenticated_client, pod_id, run["id"])
    assert fetched["status"] == "COMPLETED"
    assert fetched["execution_context"]["record"] == output


@pytest.mark.asyncio
async def test_workflow_list_and_access_respects_pod_roles(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    ctx = await create_role_visibility_context(
        authenticated_client,
        async_client,
        fixed_test_org,
        pod_name_prefix="workflow-visibility",
        custom_role="WORKFLOW_REVIEWERS",
    )
    pod_id = ctx["pod_id"]
    default_name = f"default_workflow_{uuid4().hex[:8]}"
    editor_name = f"editor_workflow_{uuid4().hex[:8]}"
    custom_name = f"custom_workflow_{uuid4().hex[:8]}"

    await _create_simple_workflow(authenticated_client, pod_id, default_name)
    await _create_simple_workflow(
        authenticated_client,
        pod_id,
        editor_name,
        visibility="RESTRICTED",
    )
    custom_workflow = await _create_simple_workflow(
        authenticated_client,
        pod_id,
        custom_name,
        visibility="RESTRICTED",
    )
    editor_workflow = await authenticated_client.get(
        f"/pods/{pod_id}/workflows/{editor_name}",
    )
    assert editor_workflow.status_code == status.HTTP_200_OK, editor_workflow.text
    editor_grant = await authenticated_client.put(
        f"/pods/{pod_id}/roles/POD_EDITOR/permissions",
        json={
            "grants": [
                {
                    "resource_type": "workflow",
                    "resource_name": editor_workflow.json()["name"],
                    "permission_ids": ["workflow.read", "workflow.update"],
                }
            ]
        },
    )
    assert editor_grant.status_code == status.HTTP_200_OK, editor_grant.text
    custom_grant = await authenticated_client.put(
        f"/pods/{pod_id}/roles/{ctx['custom_role']}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "workflow",
                    "resource_name": custom_workflow["name"],
                    "permission_ids": ["workflow.read"],
                }
            ]
        },
    )
    assert custom_grant.status_code == status.HTTP_200_OK, custom_grant.text

    viewer_list = await async_client.get(
        f"/pods/{pod_id}/workflows",
        headers=ctx["viewer_headers"],
    )
    assert viewer_list.status_code == status.HTTP_200_OK, viewer_list.text
    assert item_names(viewer_list.json()) == {default_name}

    editor_list = await async_client.get(
        f"/pods/{pod_id}/workflows",
        headers=ctx["editor_headers"],
    )
    assert editor_list.status_code == status.HTTP_200_OK, editor_list.text
    assert item_names(editor_list.json()) == {default_name, editor_name}
    editor_items = {item["name"]: item for item in editor_list.json()["items"]}
    assert set(editor_items[default_name]["allowed_actions"]) == {
        "workflow.read",
        "workflow.execute",
        "workflow.update",
    }
    assert set(editor_items[editor_name]["allowed_actions"]) == {
        "workflow.read",
        "workflow.update",
    }
    editor_get_default = await async_client.get(
        f"/pods/{pod_id}/workflows/{default_name}",
        headers=ctx["editor_headers"],
    )
    assert editor_get_default.status_code == status.HTTP_200_OK, editor_get_default.text
    assert set(editor_get_default.json()["allowed_actions"]) == {
        "workflow.read",
        "workflow.execute",
        "workflow.update",
    }
    editor_get_restricted = await async_client.get(
        f"/pods/{pod_id}/workflows/{editor_name}",
        headers=ctx["editor_headers"],
    )
    assert editor_get_restricted.status_code == status.HTTP_200_OK, (
        editor_get_restricted.text
    )
    assert set(editor_get_restricted.json()["allowed_actions"]) == {
        "workflow.read",
        "workflow.update",
    }

    custom_list = await async_client.get(
        f"/pods/{pod_id}/workflows",
        headers=ctx["custom_headers"],
    )
    assert custom_list.status_code == status.HTTP_200_OK, custom_list.text
    assert item_names(custom_list.json()) == {default_name, custom_name}
    custom_items = {item["name"]: item for item in custom_list.json()["items"]}
    assert set(custom_items[default_name]["allowed_actions"]) == {"workflow.read"}
    assert set(custom_items[custom_name]["allowed_actions"]) == {"workflow.read"}
    custom_get_restricted = await async_client.get(
        f"/pods/{pod_id}/workflows/{custom_name}",
        headers=ctx["custom_headers"],
    )
    assert custom_get_restricted.status_code == status.HTTP_200_OK, (
        custom_get_restricted.text
    )
    assert set(custom_get_restricted.json()["allowed_actions"]) == {"workflow.read"}

    viewer_get_restricted = await async_client.get(
        f"/pods/{pod_id}/workflows/{editor_name}",
        headers=ctx["viewer_headers"],
    )
    assert viewer_get_restricted.status_code == status.HTTP_403_FORBIDDEN

    viewer_edit_default = await async_client.patch(
        f"/pods/{pod_id}/workflows/{default_name}",
        json={"description": "viewer edit"},
        headers=ctx["viewer_headers"],
    )
    assert viewer_edit_default.status_code == status.HTTP_403_FORBIDDEN

    custom_edit_custom = await async_client.patch(
        f"/pods/{pod_id}/workflows/{custom_name}",
        json={"description": "custom viewer edit"},
        headers=ctx["custom_headers"],
    )
    assert custom_edit_custom.status_code == status.HTTP_403_FORBIDDEN

    editor_edit_restricted = await async_client.patch(
        f"/pods/{pod_id}/workflows/{editor_name}",
        json={"description": "editor edit"},
        headers=ctx["editor_headers"],
    )
    assert editor_edit_restricted.status_code == status.HTTP_200_OK
    assert set(editor_edit_restricted.json()["allowed_actions"]) == {
        "workflow.read",
        "workflow.update",
    }
