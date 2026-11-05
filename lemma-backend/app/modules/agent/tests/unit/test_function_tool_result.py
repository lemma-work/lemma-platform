"""What a function exposed as an agent tool returns, by where its run got to.

A JOB function still running when the tool stopped waiting was reported as
"Function X failed". The model's reasonable answer to a failure is to run it
again, which repeats whatever the function does while the first run carries on.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.infrastructure.tools.callables import function_tool_result
from app.modules.function.contracts import FunctionRunEntity, FunctionRunStatus

pytestmark = pytest.mark.unit


def _run(status: FunctionRunStatus, **fields: object) -> FunctionRunEntity:
    return FunctionRunEntity(
        id=uuid4(), function_id=uuid4(), user_id=uuid4(), status=status, **fields
    )


@pytest.mark.parametrize(
    "status", [FunctionRunStatus.PENDING, FunctionRunStatus.RUNNING]
)
def test_a_run_still_going_returns_a_handle_not_a_failure(status):
    run = _run(status)

    result = function_tool_result("import_orders", run)

    assert result["still_running"] is True
    assert result["run_id"] == str(run.id)
    assert result["status"] == status.value
    assert "do not run it again" in result["note"]
    assert f"lemma functions runs get import_orders {run.id}" in result["note"]


def test_a_finished_run_returns_its_output():
    result = function_tool_result(
        "import_orders",
        _run(FunctionRunStatus.COMPLETED, output_data={"imported": 3}),
    )

    assert result == {"imported": 3}


def test_a_failed_run_is_still_a_failure():
    with pytest.raises(RuntimeError, match="bad row"):
        function_tool_result(
            "import_orders", _run(FunctionRunStatus.FAILED, error="bad row")
        )
