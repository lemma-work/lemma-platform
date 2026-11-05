"""A pod's functions as tools an MCP host calls: one per function, as the person.

What each function is offered as, and what a run answers, are pure and tested
as such. The calls go through a gateway the test plays, which is the seam the
real one fills from the function module's contracts. Running a real function
is the function module's e2e suite's.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.domain.errors import DomainError
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.services.pod_mcp_functions import (
    FUNCTION_RUN,
    MAX_FUNCTIONS,
    RUN_FUNCTION_PREFIX,
    PersonFunctionRun,
    PodFunctions,
    RunnableFunction,
    function_tools,
    is_function_tool,
    offers_functions,
    run_payload,
)
from app.modules.agent.services.pod_mcp_service import PodMCPService
from app.modules.function.contracts import FunctionRunEntity, FunctionRunStatus
from app.modules.mcp_access.contracts import McpPrincipal, Scope

pytestmark = pytest.mark.unit

POD = uuid4()
PERSON = uuid4()

_SCORE = RunnableFunction(
    name="score_ticket",
    description="Score a support ticket's urgency.",
    input_schema={
        "type": "object",
        "properties": {"ticket_id": {"type": "string"}},
        "required": ["ticket_id"],
    },
    is_job=False,
)
_BACKFILL = RunnableFunction(
    name="backfill",
    description=None,
    input_schema={},
    is_job=True,
)


def _principal(*scopes: Scope) -> McpPrincipal:
    return McpPrincipal(
        user_id=PERSON,
        pod_id=POD,
        grant_id=uuid4(),
        client_id="client",
        client_name="ChatGPT",
        scopes=frozenset(scopes),
    )


def _run(status: FunctionRunStatus, *, output=None, error=None) -> FunctionRunEntity:
    return FunctionRunEntity(
        id=uuid4(),
        function_id=uuid4(),
        user_id=PERSON,
        status=status,
        output_data=output,
        error=error,
    )


class _Gateway:
    """The function module as the tools see it, played by the test."""

    def __init__(
        self,
        *,
        functions: list[RunnableFunction],
        runs: list[FunctionRunEntity] | None = None,
        refusal: str | None = None,
    ) -> None:
        self.functions = functions
        self.runs = list(runs or [])
        self.refusal = refusal
        self.ran: list[tuple[str, JsonObject]] = []

    async def runnable(self, *, pod_id: UUID, user_id: UUID) -> list[RunnableFunction]:
        return self.functions

    async def run(
        self, *, pod_id: UUID, name: str, input_data: JsonObject, user_id: UUID
    ) -> FunctionRunEntity:
        if self.refusal:
            raise DomainError(self.refusal, status_code=422)
        self.ran.append((name, input_data))
        return self.runs.pop(0)

    async def read_run(
        self, *, pod_id: UUID, run_id: UUID, user_id: UUID
    ) -> PersonFunctionRun | None:
        if not self.runs:
            return None
        return PersonFunctionRun(function="backfill", run=self.runs.pop(0))


async def _no_sleep(_seconds: float) -> None:
    return None


def _functions(gateway: _Gateway) -> PodFunctions:
    return PodFunctions(lambda: None, gateway=gateway, sleep=_no_sleep)


# --- What each function is offered as ------------------------------------------


def test_running_a_function_needs_a_connection_that_may_change_things():
    assert offers_functions(_principal(Scope.READ, Scope.WRITE))
    assert not offers_functions(_principal(Scope.READ))
    assert not offers_functions(None)


def test_each_function_is_a_tool_with_its_own_input_schema():
    score, backfill, read_run = function_tools([_SCORE, _BACKFILL])
    assert score.name == f"{RUN_FUNCTION_PREFIX}score_ticket"
    assert score.title == "Run score_ticket"
    assert score.description.startswith("Score a support ticket's urgency.")
    assert score.input_schema["required"] == ["ticket_id"]
    # A function with no declared input takes no arguments, not a broken schema.
    assert backfill.input_schema == {"type": "object", "properties": {}}
    assert read_run.name == FUNCTION_RUN
    assert all(is_function_tool(tool.name) for tool in (score, backfill, read_run))
    assert not is_function_tool("lemma_pod_query")


def test_a_run_is_confirmed_with_the_person_and_a_check_is_not():
    score, _, read_run = function_tools([_SCORE, _BACKFILL])
    assert score.annotations.destructive_hint is True
    assert score.annotations.open_world_hint is True
    assert score.annotations.read_only_hint is False
    assert read_run.annotations.read_only_hint is True


def test_no_more_functions_are_tools_than_a_list_can_hold():
    many = [
        RunnableFunction(
            name=f"f{n:02}", description=None, input_schema={}, is_job=False
        )
        for n in range(MAX_FUNCTIONS + 5)
    ]
    tools = function_tools(many)
    assert len(tools) == MAX_FUNCTIONS + 1


def test_a_long_function_name_still_makes_a_valid_tool_name():
    long = RunnableFunction(
        name="a" * 200, description=None, input_schema={}, is_job=False
    )
    assert len(long.tool_name) <= 64
    assert long.tool_name.startswith(RUN_FUNCTION_PREFIX)


def test_no_functions_means_no_function_tools():
    assert function_tools([]) == []


# --- What a run answers ----------------------------------------------------------


def test_a_finished_run_answers_with_its_output():
    payload = run_payload(
        "score_ticket", _run(FunctionRunStatus.COMPLETED, output={"urgency": 4})
    )
    assert payload["success"] is True
    assert payload["output"] == {"urgency": 4}


def test_a_run_still_going_is_not_a_failure_and_says_not_to_run_it_again():
    payload = run_payload("backfill", _run(FunctionRunStatus.RUNNING))
    assert payload["success"] is True
    assert payload["status"] == "RUNNING"
    assert "do not run it again" in payload["message"]
    assert FUNCTION_RUN in payload["message"]


def test_a_failed_run_says_why():
    payload = run_payload(
        "backfill", _run(FunctionRunStatus.FAILED, error="Timed out.")
    )
    assert payload == {
        "function": "backfill",
        "run_id": payload["run_id"],
        "success": False,
        "status": "FAILED",
        "error": "Timed out.",
    }


# --- Calls ---------------------------------------------------------------------


async def test_calling_a_function_runs_it_as_the_person_with_the_arguments_given():
    gateway = _Gateway(
        functions=[_SCORE],
        runs=[_run(FunctionRunStatus.COMPLETED, output={"urgency": 4})],
    )
    result = await _functions(gateway).call(
        _SCORE.tool_name, {"ticket_id": "T-1"}, principal=_principal(Scope.WRITE)
    )
    assert gateway.ran == [("score_ticket", {"ticket_id": "T-1"})]
    assert result.structured_content["output"] == {"urgency": 4}


async def test_a_job_is_waited_on_until_it_finishes():
    gateway = _Gateway(
        functions=[_BACKFILL],
        runs=[
            _run(FunctionRunStatus.PENDING),
            _run(FunctionRunStatus.RUNNING),
            _run(FunctionRunStatus.COMPLETED, output={"rows": 12}),
        ],
    )
    result = await _functions(gateway).call(
        _BACKFILL.tool_name, {}, principal=_principal(Scope.WRITE)
    )
    assert result.structured_content["status"] == "COMPLETED"
    assert result.structured_content["output"] == {"rows": 12}


async def test_a_function_the_person_may_not_run_is_refused():
    result = await _functions(_Gateway(functions=[_SCORE])).call(
        f"{RUN_FUNCTION_PREFIX}delete_everything", {}, principal=_principal(Scope.WRITE)
    )
    assert result.is_error
    assert "may not run it" in result.structured_content["error"]


async def test_a_run_the_function_module_refuses_says_why():
    gateway = _Gateway(functions=[_SCORE], refusal="ticket_id is required")
    result = await _functions(gateway).call(
        _SCORE.tool_name, {}, principal=_principal(Scope.WRITE)
    )
    assert result.is_error
    assert result.structured_content["error"] == "ticket_id is required"


async def test_someone_elses_run_cannot_be_read():
    result = await _functions(_Gateway(functions=[_SCORE])).call(
        FUNCTION_RUN, {"run_id": str(uuid4())}, principal=_principal(Scope.WRITE)
    )
    assert result.is_error
    assert "No function run of yours" in result.structured_content["error"]


async def test_reading_a_run_names_the_function_it_ran():
    gateway = _Gateway(
        functions=[], runs=[_run(FunctionRunStatus.COMPLETED, output={"rows": 3})]
    )
    result = await _functions(gateway).call(
        FUNCTION_RUN, {"run_id": str(uuid4())}, principal=_principal(Scope.WRITE)
    )
    assert result.structured_content["function"] == "backfill"
    assert result.structured_content["output"] == {"rows": 3}


async def test_a_read_only_connection_that_runs_a_function_anyway_is_refused():
    result = await PodMCPService().call_tool(
        pod_id=POD,
        token="",
        name=_SCORE.tool_name,
        arguments={"ticket_id": "T-1"},
        principal=_principal(Scope.READ),
    )
    assert result.is_error
    assert "allowed to change things" in result.content[0].text
