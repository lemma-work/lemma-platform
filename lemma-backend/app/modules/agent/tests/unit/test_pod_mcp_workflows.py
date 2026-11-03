"""A pod's workflows from an MCP host: starting, following, and filling in forms.

The rule worth pinning is whose forms the host may fill in: the ones on runs
the person started, never a decision someone else's run is waiting on. The
calls go through a gateway the test plays, the seam the real one fills from
the workflow module's contracts.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.services.pod_mcp_service import PodMCPService
from app.modules.agent.services.pod_mcp_workflows import (
    START_WORKFLOW,
    SUBMIT_FORM,
    WAITING_ON_ME,
    WORKFLOW_RUN,
    PodWorkflows,
    is_workflow_tool,
    run_payload,
    submit_refusal,
    workflow_tools,
)
from app.modules.mcp_access.contracts import McpPrincipal, Scope
from app.modules.workflow.contracts.person_runs import (
    PersonWorkflowRun,
    PodWorkflow,
    RunWait,
    WorkflowRefused,
)

pytestmark = pytest.mark.unit

POD = uuid4()
PERSON = uuid4()
SOMEONE = uuid4()

_INTAKE = PodWorkflow(name="intake", description="Log a new request.", may_start=True)
_PAYROLL = PodWorkflow(name="payroll", description=None, may_start=False)
_FORM = {"title": "New request", "fields": [{"name": "title", "type": "string"}]}


def _principal(*scopes: Scope) -> McpPrincipal:
    return McpPrincipal(
        user_id=PERSON,
        pod_id=POD,
        grant_id=uuid4(),
        client_id="client",
        client_name="ChatGPT",
        scopes=frozenset(scopes),
    )


def _run(
    *,
    started_by: UUID = PERSON,
    status: str = "WAITING",
    kind: str | None = "HUMAN",
    on_this_person: bool = True,
    run_id: UUID | None = None,
) -> PersonWorkflowRun:
    return PersonWorkflowRun(
        id=run_id or uuid4(),
        workflow="intake",
        status=status,
        started_by=started_by,
        error=None,
        waiting=(
            RunWait(
                node_id="request_form",
                kind=kind,
                payload=_FORM,
                on_this_person=on_this_person,
            )
            if kind
            else None
        ),
    )


class _Gateway:
    """The workflow module as the tools see it, played by the test."""

    def __init__(
        self,
        *,
        runs: list[PersonWorkflowRun] | None = None,
        submitted: tuple[bool, str] = (True, "Submitted."),
        refusal: str | None = None,
    ) -> None:
        self.runs = list(runs or [])
        self.submitted = submitted
        self.refusal = refusal
        self.submits: list[tuple[UUID, str, JsonObject]] = []

    async def workflows(self, principal: McpPrincipal) -> list[PodWorkflow]:
        return [_INTAKE, _PAYROLL]

    async def start(self, principal: McpPrincipal, name: str) -> PersonWorkflowRun:
        if self.refusal:
            raise WorkflowRefused(self.refusal)
        return self.runs.pop(0)

    async def run(self, principal: McpPrincipal, run_id: UUID) -> PersonWorkflowRun:
        return self.runs.pop(0)

    async def waiting(self, principal: McpPrincipal) -> list[PersonWorkflowRun]:
        return self.runs

    async def submit(
        self, principal: McpPrincipal, *, run_id: UUID, node_id: str, inputs: JsonObject
    ) -> tuple[bool, str]:
        self.submits.append((run_id, node_id, inputs))
        return self.submitted


def _workflows(gateway: _Gateway) -> PodWorkflows:
    return PodWorkflows(
        lambda: None,
        gateway=gateway,
        workflow_url=lambda pod, name: f"https://lemma.test/{name}",
    )


# --- What is offered, and to whom --------------------------------------------------


def test_a_read_only_connection_can_follow_runs_but_not_start_them():
    names = [tool.name for tool in workflow_tools([_INTAKE], may_change=False)]
    assert names == [WORKFLOW_RUN, WAITING_ON_ME]


def test_starting_names_only_the_workflows_the_person_may_start():
    start, submit, *reads = workflow_tools([_INTAKE, _PAYROLL], may_change=True)
    assert (start.name, submit.name) == (START_WORKFLOW, SUBMIT_FORM)
    assert start.input_schema["properties"]["workflow"]["enum"] == ["intake"]
    assert "- intake: Log a new request." in start.description
    assert "payroll" not in start.description
    assert start.annotations.destructive_hint is True
    assert submit.annotations.destructive_hint is True
    assert all(tool.annotations.read_only_hint for tool in reads)
    assert all(is_workflow_tool(tool.name) for tool in (start, submit, *reads))


def test_with_nothing_to_start_only_the_reading_tools_are_offered():
    names = [tool.name for tool in workflow_tools([_PAYROLL], may_change=True)]
    assert names == [WORKFLOW_RUN, WAITING_ON_ME]


def test_a_pod_with_no_workflows_offers_no_workflow_tools():
    assert workflow_tools([], may_change=True) == []


# --- Whose forms the host may fill in ------------------------------------------------


def test_the_form_on_a_run_the_person_started_is_theirs_to_fill_in():
    assert submit_refusal(_run(), user_id=PERSON) is None


def test_a_form_in_someone_elses_run_is_answered_in_lemma():
    refusal = submit_refusal(_run(started_by=SOMEONE), user_id=PERSON)
    assert refusal is not None
    assert "in Lemma" in refusal


def test_a_form_waiting_on_someone_else_is_not_the_persons():
    assert submit_refusal(_run(on_this_person=False), user_id=PERSON) == (
        "That form is not waiting on you."
    )


def test_a_run_waiting_on_a_step_rather_than_a_form_has_nothing_to_submit():
    assert submit_refusal(_run(kind="AGENT"), user_id=PERSON) == (
        "That run is not waiting on a form."
    )
    assert submit_refusal(_run(kind=None), user_id=PERSON) == (
        "That run is not waiting on a form."
    )


def test_a_run_waiting_on_its_form_shows_the_form_and_how_to_fill_it_in():
    payload = run_payload(_run(), url="u", user_id=PERSON)
    assert payload["waiting"]["form"] == _FORM
    assert payload["waiting"]["node_id"] == "request_form"
    assert SUBMIT_FORM in payload["message"]


def test_a_step_that_is_not_a_form_carries_no_form():
    payload = run_payload(_run(kind="FUNCTION"), url="u", user_id=PERSON)
    assert "form" not in payload["waiting"]
    assert "message" not in payload


# --- Calls -----------------------------------------------------------------------


async def test_starting_a_workflow_that_begins_with_a_form_returns_the_form():
    gateway = _Gateway(runs=[_run()])
    result = await _workflows(gateway).call(
        START_WORKFLOW, {"workflow": "intake"}, principal=_principal(Scope.WRITE)
    )
    content = result.structured_content
    assert content["status"] == "WAITING"
    assert content["waiting"]["form"] == _FORM
    assert content["url"] == "https://lemma.test/intake"


async def test_submitting_uses_the_node_the_run_waits_on_not_one_the_model_names():
    run_id = uuid4()
    gateway = _Gateway(
        runs=[_run(run_id=run_id), _run(run_id=run_id, status="RUNNING", kind=None)]
    )
    result = await _workflows(gateway).call(
        SUBMIT_FORM,
        {"run_id": str(run_id), "inputs": {"title": "Laptop"}, "node_id": "other"},
        principal=_principal(Scope.WRITE),
    )
    assert gateway.submits == [(run_id, "request_form", {"title": "Laptop"})]
    assert result.structured_content["status"] == "RUNNING"


async def test_a_form_in_someone_elses_run_is_not_submitted():
    gateway = _Gateway(runs=[_run(started_by=SOMEONE)])
    result = await _workflows(gateway).call(
        SUBMIT_FORM,
        {"run_id": str(uuid4()), "inputs": {"approved": True}},
        principal=_principal(Scope.WRITE),
    )
    assert result.is_error
    assert gateway.submits == []
    assert "https://lemma.test/intake" in result.structured_content["error"]


async def test_a_submission_the_form_rejects_says_which_field():
    gateway = _Gateway(runs=[_run()], submitted=(False, "title is required"))
    result = await _workflows(gateway).call(
        SUBMIT_FORM,
        {"run_id": str(uuid4()), "inputs": {}},
        principal=_principal(Scope.WRITE),
    )
    assert result.is_error
    assert result.structured_content["error"] == "title is required"


async def test_a_workflow_the_person_may_not_start_is_refused():
    gateway = _Gateway(refusal="No workflow you may start is called 'payroll'.")
    result = await _workflows(gateway).call(
        START_WORKFLOW, {"workflow": "payroll"}, principal=_principal(Scope.WRITE)
    )
    assert result.is_error
    assert "payroll" in result.structured_content["error"]


async def test_the_waiting_list_says_where_to_answer_each_form():
    gateway = _Gateway(runs=[_run(started_by=SOMEONE)])
    result = await _workflows(gateway).call(
        WAITING_ON_ME, {}, principal=_principal(Scope.READ)
    )
    [waiting] = result.structured_content["waiting"]
    assert waiting["url"] == "https://lemma.test/intake"
    assert "in Lemma" in waiting["message"]


async def test_arguments_are_checked_before_anything_is_started():
    result = await _workflows(_Gateway()).call(
        START_WORKFLOW, {}, principal=_principal(Scope.WRITE)
    )
    assert result.is_error
    assert result.structured_content["error"].startswith("workflow")


@pytest.mark.parametrize("name", [START_WORKFLOW, SUBMIT_FORM])
async def test_a_read_only_connection_cannot_start_or_submit(name):
    result = await PodMCPService().call_tool(
        pod_id=POD,
        token="",
        name=name,
        arguments={"workflow": "intake"},
        principal=_principal(Scope.READ),
    )
    assert result.is_error
    assert "allowed to change things" in result.content[0].text
