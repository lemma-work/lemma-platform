"""Scheduling the pod's own assistant, and saying what it should do.

Two things, tested together because they only make sense together.

The assistant is named on the wire by ``POD_DEFAULT``, which is a selector
rather than a name -- it predates the assistant having one. It is resolved to
the row like any other agent, which is the whole of the first half: there is no
second target mechanism any more, and a schedule pointed at the assistant is a
schedule with an ``agent_id``.

The second half is the instruction. An agent's standing instruction says what it
is *for*; a schedule's says what *this firing* is for. An agent that has the
first may be woken without the second. The assistant has no standing instruction
-- it is whoever the conversation needs it to be -- so waking it without one
wakes it to a trigger payload and no job.

That rule is stated against the agent's instruction rather than against which
agent it is. More general, and today exactly equivalent: a blank instruction is
refused everywhere an agent can be created, so the assistant is the only agent
that can have one.

See PS-SCHED-004.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_NAME,
    POD_DEFAULT_AGENT_SELECTOR,
)
from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.domain.schedule import (
    ScheduleCreateEntity,
    ScheduleType,
    ScheduleUpdateEntity,
)
from app.modules.schedule.services.schedule_service import (
    ScheduleService,
    normalize_agent_selector,
)
from app.modules.schedule.services.schedule_target_policy import (
    validate_instruction_survives_update,
    validate_target_instruction,
)

pytestmark = pytest.mark.unit


def _target(*, instruction: str | None, pod_id=None, name="triage"):
    """A resolved schedule target, which is what the rules are asked about."""
    return SimpleNamespace(
        id=uuid4(), pod_id=pod_id or uuid4(), name=name, instruction=instruction
    )


def _assistant(pod_id=None):
    """The pod's own assistant: a row whose id is its pod's, with no standing
    instruction of its own."""
    pod_id = pod_id or uuid4()
    return SimpleNamespace(
        id=pod_id, pod_id=pod_id, name=DEFAULT_POD_AGENT_NAME, instruction=""
    )


def _service(agent) -> ScheduleService:
    service = ScheduleService(
        uow=AsyncMock(),
        schedule_repository=AsyncMock(),
        external_schedule_writer=AsyncMock(),
    )
    service._get_agent_by_name = AsyncMock(return_value=agent)
    return service


# --- naming the target ------------------------------------------------------


@pytest.mark.parametrize(
    "selector", [POD_DEFAULT_AGENT_SELECTOR, DEFAULT_POD_AGENT_NAME]
)
def test_both_wire_selectors_name_the_assistants_row(selector):
    assert normalize_agent_selector(selector) == DEFAULT_POD_AGENT_NAME


@pytest.mark.parametrize("name", ["triage", "pod-default", "POD_DEFAULT_AGENT"])
def test_an_ordinary_agent_name_is_left_alone(name):
    assert normalize_agent_selector(name) == name


@pytest.mark.asyncio
async def test_the_selector_resolves_to_a_row_like_any_other_agent():
    """The point of the change, in one assertion.

    The assistant used to resolve to no row at all, so the lookup every other
    agent goes through raised "Agent target not found in pod" for it -- which is
    what scheduling it used to hit.
    """
    assistant = _assistant()
    service = _service(assistant)

    fields = await service._agent_target_fields(
        pod_id=assistant.pod_id,
        agent_name=POD_DEFAULT_AGENT_SELECTOR,
        instruction="Summarise yesterday's open tickets.",
    )

    assert fields == {"agent_id": assistant.id, "workflow_id": None}
    # Looked up by the row's name, not by the wire selector.
    service._get_agent_by_name.assert_awaited_once_with(
        pod_id=assistant.pod_id, agent_name=DEFAULT_POD_AGENT_NAME
    )


@pytest.mark.asyncio
async def test_naming_an_agent_clears_a_workflow_target():
    """A schedule wakes exactly one thing, so setting one clears the other."""
    agent = _target(instruction="Answer support questions.")
    service = _service(agent)

    fields = await service._agent_target_fields(
        pod_id=agent.pod_id, agent_name="triage", instruction=None
    )

    assert fields == {"agent_id": agent.id, "workflow_id": None}


# --- what the firing is for -------------------------------------------------


def test_waking_the_assistant_without_an_instruction_is_refused():
    with pytest.raises(ScheduleValidationError):
        validate_target_instruction(_assistant(), None)


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_whitespace_is_not_an_instruction(blank):
    with pytest.raises(ScheduleValidationError):
        validate_target_instruction(_assistant(), blank)


def test_the_assistant_may_be_woken_once_it_is_told_what_to_do():
    validate_target_instruction(_assistant(), "Check the overnight queue.")


def test_an_agent_that_is_its_own_instruction_needs_none():
    """The rule is about the target's standing instruction, not its identity."""
    validate_target_instruction(_target(instruction="Triage support email."), None)


def test_a_workflow_target_is_never_asked():
    """A workflow is its own set of steps and has nowhere to put a sentence."""
    validate_target_instruction(None, None)


# --- edits ------------------------------------------------------------------


def test_an_edit_cannot_empty_the_assistants_instruction():
    with pytest.raises(ScheduleValidationError):
        validate_instruction_survives_update(
            _assistant(), ScheduleUpdateEntity(instruction="")
        )


def test_an_edit_that_never_mentions_the_instruction_leaves_it_alone():
    validate_instruction_survives_update(
        _assistant(), ScheduleUpdateEntity(name="renamed")
    )


def test_clearing_the_instruction_while_retargeting_to_a_workflow_is_fine():
    """Nothing is lost: a workflow never needed the sentence."""
    validate_instruction_survives_update(
        None, ScheduleUpdateEntity(workflow_name="digest", instruction="")
    )


# --- exactly one target -----------------------------------------------------


@pytest.mark.asyncio
async def test_two_targets_at_once_is_refused():
    service = _service(_target(instruction="x"))
    schedule = ScheduleCreateEntity(
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.TIME,
        agent_id=uuid4(),
        workflow_id=uuid4(),
        config={"cron": "0 9 * * *"},
    )

    with pytest.raises(ScheduleValidationError):
        await service._validate_target(schedule)


@pytest.mark.asyncio
async def test_a_schedule_that_wakes_nothing_is_allowed():
    """Internal wait/timeout schedules legitimately have no target."""
    service = _service(_target(instruction="x"))
    schedule = ScheduleCreateEntity(
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.TIME,
        config={"cron": "0 9 * * *"},
    )

    await service._validate_target(schedule)
