"""Scheduling the pod's default assistant, and saying what it should do.

Two things are being tested together because they only make sense together. The
default assistant has no ``agents`` row, so a schedule cannot name it through
the ``agent_id`` foreign key — that is the first half. And it has no standing
instruction either (a named agent *is* its instruction; Lem's is the empty
string), so a schedule that wakes it and says nothing wakes it to a JSON payload
and no job — that is the second.

See PS-SCHED-004.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_NAME,
    POD_DEFAULT_AGENT_SELECTOR,
)
from app.modules.schedule.api.schemas.schedule_schemas import (
    CreateScheduleRequest,
    UpdateScheduleRequest,
)
from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.domain.schedule import (
    ScheduleCreateEntity,
    ScheduleEntity,
    ScheduleType,
    ScheduleUpdateEntity,
    is_pod_default_agent_target,
)
from app.modules.schedule.services.schedule_service import ScheduleService


def _service() -> ScheduleService:
    return ScheduleService(
        uow=AsyncMock(),
        schedule_repository=AsyncMock(),
        external_schedule_writer=AsyncMock(),
    )


# --- naming the target ------------------------------------------------------


@pytest.mark.parametrize(
    "selector", [POD_DEFAULT_AGENT_SELECTOR, DEFAULT_POD_AGENT_NAME]
)
def test_both_wire_selectors_name_the_default_assistant(selector):
    """The same two spellings the conversation API has always accepted.

    A third spelling here would be a second vocabulary for one thing.
    """
    assert is_pod_default_agent_target(selector)


@pytest.mark.parametrize("name", [None, "", "triage", "pod_defaults", "POD-DEFAULT"])
def test_an_ordinary_agent_name_is_not_the_selector(name):
    assert not is_pod_default_agent_target(name)


@pytest.mark.asyncio
async def test_resolving_the_selector_sets_the_flag_and_looks_nothing_up():
    """The lookup every other agent goes through would find no row.

    ``_get_agent_by_name`` raises "Agent target not found in pod" for anything
    it cannot resolve, which is what scheduling Lem used to hit.
    """
    service = _service()
    service._get_agent_by_name = AsyncMock(  # type: ignore[method-assign]
        side_effect=AssertionError("must not look up the default assistant")
    )

    resolved = await service._resolve_create_target(
        ScheduleCreateEntity(
            user_id=uuid4(),
            pod_id=uuid4(),
            schedule_type=ScheduleType.TIME,
            agent_name=POD_DEFAULT_AGENT_SELECTOR,
            instruction="Summarise yesterday's tickets.",
            config={"cron": "0 9 * * 1-5"},
        )
    )

    assert resolved.targets_pod_default is True
    assert resolved.agent_id is None
    assert resolved.workflow_id is None


@pytest.mark.asyncio
async def test_retargeting_to_a_named_agent_clears_the_flag():
    """All three target columns move together, or the row grows two targets."""
    service = _service()
    agent_id = uuid4()
    service._get_agent_by_name = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(id=agent_id)
    )
    existing = ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.TIME,
        targets_pod_default=True,
        instruction="Summarise yesterday's tickets.",
        config={"cron": "0 9 * * 1-5"},
    )

    update_data = await service._resolve_update_target(
        existing, ScheduleUpdateEntity(agent_name="triage")
    )

    assert update_data["agent_id"] == agent_id
    assert update_data["targets_pod_default"] is False
    assert update_data["workflow_id"] is None


@pytest.mark.asyncio
async def test_retargeting_to_a_workflow_clears_the_flag_too():
    service = _service()
    workflow_id = uuid4()
    service._get_workflow_by_name = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(id=workflow_id, event_trigger_id=None)
    )
    existing = ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.TIME,
        targets_pod_default=True,
        instruction="Summarise yesterday's tickets.",
        config={"cron": "0 9 * * 1-5"},
    )

    update_data = await service._resolve_update_target(
        existing, ScheduleUpdateEntity(workflow_name="nightly_digest")
    )

    assert update_data["workflow_id"] == workflow_id
    assert update_data["targets_pod_default"] is False
    assert update_data["agent_id"] is None


def test_the_entity_counts_the_flag_as_a_target():
    """``has_target`` is what the dispatcher gates on before claiming a run."""
    schedule = ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.TIME,
        targets_pod_default=True,
        instruction="Do the thing.",
    )
    assert schedule.has_target
    assert not schedule.model_copy(update={"targets_pod_default": False}).has_target


@pytest.mark.asyncio
async def test_two_targets_at_once_is_refused():
    service = _service()

    with pytest.raises(ScheduleValidationError, match="either an agent or workflow"):
        await service._validate_target(
            ScheduleCreateEntity(
                user_id=uuid4(),
                pod_id=uuid4(),
                schedule_type=ScheduleType.TIME,
                agent_id=uuid4(),
                targets_pod_default=True,
                instruction="Do the thing.",
            )
        )


# --- requiring the instruction ----------------------------------------------


def test_creating_a_lem_trigger_without_an_instruction_is_refused():
    """Refused at creation, not discovered at 9am on a run that did nothing."""
    with pytest.raises(ValidationError, match="require an instruction"):
        CreateScheduleRequest(
            schedule_type=ScheduleType.TIME,
            agent_name=POD_DEFAULT_AGENT_SELECTOR,
            config={"cron": "0 9 * * 1-5"},
        )


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_whitespace_is_not_an_instruction(blank):
    with pytest.raises(ValidationError, match="require an instruction"):
        CreateScheduleRequest(
            schedule_type=ScheduleType.TIME,
            agent_name=POD_DEFAULT_AGENT_SELECTOR,
            instruction=blank,
            config={"cron": "0 9 * * 1-5"},
        )


def test_a_named_agent_needs_no_instruction():
    """It already is one. The schedule's sentence only ever adds to it."""
    request = CreateScheduleRequest(
        schedule_type=ScheduleType.TIME,
        agent_name="triage",
        config={"cron": "0 9 * * 1-5"},
    )
    assert request.instruction is None


def test_retargeting_onto_lem_carries_the_same_requirement():
    with pytest.raises(ValidationError, match="require an instruction"):
        UpdateScheduleRequest(agent_name=POD_DEFAULT_AGENT_SELECTOR, instruction="")


@pytest.mark.asyncio
async def test_an_edit_cannot_empty_a_lem_triggers_instruction():
    """The request schema cannot see the existing target; the service can.

    Clearing the words on a trigger that stays pointed at Lem leaves exactly the
    schedule the create path refuses to make.
    """
    service = _service()
    existing = ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.TIME,
        targets_pod_default=True,
        instruction="Summarise yesterday's tickets.",
        config={"cron": "0 9 * * 1-5"},
    )

    with pytest.raises(ScheduleValidationError, match="require an instruction"):
        await service._resolve_update_target(
            existing, ScheduleUpdateEntity(instruction="   ")
        )


@pytest.mark.asyncio
async def test_clearing_the_instruction_while_retargeting_away_is_fine():
    """A named agent does not need the sentence, so dropping it is not a loss."""
    service = _service()
    service._get_agent_by_name = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(id=uuid4())
    )
    existing = ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.TIME,
        targets_pod_default=True,
        instruction="Summarise yesterday's tickets.",
        config={"cron": "0 9 * * 1-5"},
    )

    update_data = await service._resolve_update_target(
        existing, ScheduleUpdateEntity(agent_name="triage", instruction="")
    )

    assert update_data["targets_pod_default"] is False


@pytest.mark.asyncio
async def test_the_service_refuses_it_too_so_bundle_import_cannot_bypass_the_rule():
    """Bundle import builds the entity directly and never sees the request schema.

    A hand-written bundle is exactly where a Lem trigger with nothing to do
    would come from.
    """
    service = _service()

    with pytest.raises(ScheduleValidationError, match="require an instruction"):
        await service._validate_target(
            ScheduleCreateEntity(
                user_id=uuid4(),
                pod_id=uuid4(),
                schedule_type=ScheduleType.TIME,
                targets_pod_default=True,
            )
        )
