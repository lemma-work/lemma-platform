"""FunctionExecutor node outcome: a dispatched run (any function type) suspends
the workflow on a FUNCTION wait; a non-run result advances inline."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.workflow.domain.wait import WorkflowRunWaitType
from app.modules.workflow.execution.executors.function import FunctionExecutor
from app.modules.workflow.execution.outcome import Advance, Suspend

pytestmark = pytest.mark.asyncio


def _node():
    return SimpleNamespace(
        config=SimpleNamespace(function_name="echo", input_mapping={})
    )


def _step(result):
    async def _execute_function(name, inputs, pod_id, user_id, ctx=None):
        return result

    return SimpleNamespace(
        context=SimpleNamespace(resolve_inputs=lambda mapping: {"a": 1}),
        function=SimpleNamespace(execute_function=_execute_function),
        pod_id=uuid4(),
        user_id=uuid4(),
        authz_ctx=None,
    )


async def test_job_function_suspends_with_function_wait():
    run_id = uuid4()
    outcome = await FunctionExecutor().execute(
        _node(),
        _step({"run_id": str(run_id), "status": "RUNNING", "function_type": "JOB"}),
    )
    assert isinstance(outcome, Suspend)
    assert outcome.wait.wait_type == WorkflowRunWaitType.FUNCTION
    assert outcome.wait.external_ref == str(run_id)


async def test_api_function_advances_inline():
    outcome = await FunctionExecutor().execute(_node(), _step({"done": True}))
    assert isinstance(outcome, Advance)
    assert outcome.output == {"done": True}


async def test_pending_run_suspends_regardless_of_type():
    # API functions are now dispatch-and-suspend too (the engine releases its
    # run-row lock across the sandbox call), so a pending run of any type waits.
    run_id = uuid4()
    outcome = await FunctionExecutor().execute(
        _node(),
        _step({"run_id": str(run_id), "status": "PENDING", "function_type": "API"}),
    )
    assert isinstance(outcome, Suspend)
    assert outcome.wait.external_ref == str(run_id)


async def test_non_dict_result_wrapped_in_advance():
    outcome = await FunctionExecutor().execute(_node(), _step("plain-string"))
    assert isinstance(outcome, Advance)
    assert outcome.output == {"result": "plain-string"}
