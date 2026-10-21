"""How long each kind of wait may stay ACTIVE before its expiry item fails the run."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.config import settings
from app.modules.workflow.domain.wait import WorkflowRunWaitEntity, WorkflowRunWaitType
from app.modules.workflow.execution.wait_ceiling import (
    HUMAN_WAIT_CEILING_MULTIPLIER,
    ceiling_seconds,
    expires_at,
    expiry_error,
)

pytestmark = pytest.mark.unit


def _wait(wait_type: WorkflowRunWaitType, **payload: object) -> WorkflowRunWaitEntity:
    return WorkflowRunWaitEntity(
        run_id=uuid4(),
        flow_id=uuid4(),
        pod_id=uuid4(),
        node_id="n",
        wait_type=wait_type,
        payload=dict(payload),
    )


@pytest.mark.parametrize(
    "wait_type", [WorkflowRunWaitType.TIME, WorkflowRunWaitType.QUEUED]
)
def test_a_timer_or_a_run_waiting_its_turn_never_expires(wait_type) -> None:
    """A wait-until sits as long as it was told to; a parked run has not hung."""
    assert ceiling_seconds(_wait(wait_type)) is None
    assert expires_at(_wait(wait_type), now=datetime.now(timezone.utc)) is None


@pytest.mark.parametrize(
    "wait_type", [WorkflowRunWaitType.FUNCTION, WorkflowRunWaitType.DECISION]
)
def test_machine_waits_get_the_deployment_ceiling(wait_type) -> None:
    assert ceiling_seconds(_wait(wait_type)) == settings.workflow_wait_max_age_seconds


def test_a_form_gets_the_human_ceiling_not_the_machine_one() -> None:
    """A form waiting two days is ordinary, not a fault."""
    assert (
        ceiling_seconds(_wait(WorkflowRunWaitType.HUMAN))
        == settings.workflow_wait_max_age_seconds * HUMAN_WAIT_CEILING_MULTIPLIER
    )


def test_a_step_timeout_replaces_the_deployment_ceiling() -> None:
    assert (
        ceiling_seconds(_wait(WorkflowRunWaitType.AGENT, timeout_seconds=1200)) == 1200
    )


def test_the_expiry_is_counted_from_when_the_wait_began() -> None:
    wait = _wait(WorkflowRunWaitType.FUNCTION)
    wait.created_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert expires_at(
        wait, now=datetime.now(timezone.utc)
    ) == wait.created_at + timedelta(seconds=settings.workflow_wait_max_age_seconds)


def test_an_abandoned_form_says_nobody_answered() -> None:
    """Not "human step did not finish", which reads as a system fault."""
    message = expiry_error(_wait(WorkflowRunWaitType.HUMAN), 72 * 3600)
    assert message == "Nobody answered this form within 72h, so the run was stopped."
    assert "did not finish within 20 min" in expiry_error(
        _wait(WorkflowRunWaitType.FUNCTION), 1200
    )
