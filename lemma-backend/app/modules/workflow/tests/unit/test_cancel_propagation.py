"""Cancelling a run stops the work it was waiting on.

Cancel used to end the run and its wait row and then leave the agent or
function running — the completion event would later find no ACTIVE wait and be
dropped with a log line. That is invisible from the outside and expensive:
a cancelled agent step kept burning tokens on an answer already discarded.
"""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)
from app.modules.workflow.execution.engine import WorkflowEngine

pytestmark = pytest.mark.anyio


def _engine() -> WorkflowEngine:
    uow = Mock()
    uow.commit = AsyncMock()
    uow.session = Mock()
    engine = WorkflowEngine(
        uow,
        agent_adapter=Mock(),
        function_adapter=Mock(),
        schedule_adapter=Mock(),
    )
    engine.agent_adapter.stop_conversation = AsyncMock()
    engine.function_adapter.cancel_run = AsyncMock()
    return engine


def _wait(wait_type: WorkflowRunWaitType, external_ref: str | None):
    return WorkflowRunWaitEntity(
        run_id=uuid4(),
        flow_id=uuid4(),
        pod_id=uuid4(),
        node_id="node",
        wait_type=wait_type,
        external_ref=external_ref,
    )


def _run(user_id):
    return Mock(id=uuid4(), user_id=user_id)


async def test_cancelling_an_agent_wait_stops_the_conversation():
    engine = _engine()
    user_id = uuid4()
    run = _run(user_id)
    wait = _wait(WorkflowRunWaitType.AGENT, str(uuid4()))

    await engine._stop_underlying_work(run, wait)

    engine.agent_adapter.stop_conversation.assert_awaited_once()
    args = engine.agent_adapter.stop_conversation.await_args.args
    assert str(args[0]) == wait.external_ref
    assert args[1] == user_id
    engine.function_adapter.cancel_run.assert_not_awaited()


async def test_cancelling_a_function_wait_cancels_the_run():
    engine = _engine()
    run = _run(uuid4())
    wait = _wait(WorkflowRunWaitType.FUNCTION, str(uuid4()))

    await engine._stop_underlying_work(run, wait)

    engine.function_adapter.cancel_run.assert_awaited_once()
    assert str(engine.function_adapter.cancel_run.await_args.args[0]) == wait.external_ref
    engine.agent_adapter.stop_conversation.assert_not_awaited()


async def test_a_human_wait_has_no_underlying_work_to_stop():
    engine = _engine()
    await engine._stop_underlying_work(_run(uuid4()), _wait(WorkflowRunWaitType.HUMAN, None))

    engine.agent_adapter.stop_conversation.assert_not_awaited()
    engine.function_adapter.cancel_run.assert_not_awaited()


async def test_a_failing_stop_does_not_prevent_the_cancel():
    """The run is being cancelled either way. The underlying work may finish in
    the same instant, and its late completion is dropped regardless — so a
    failure here must not surface as a failed cancel."""
    engine = _engine()
    engine.agent_adapter.stop_conversation = AsyncMock(side_effect=RuntimeError("gone"))

    await engine._stop_underlying_work(_run(uuid4()), _wait(WorkflowRunWaitType.AGENT, str(uuid4())))
