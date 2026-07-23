from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.function.domain.entities import FunctionType
from app.modules.pod_bundle.domain.state import PlanStep, StepAction, StepKind
from app.modules.pod_bundle.infrastructure.function_builder import FunctionStepRunner


pytestmark = pytest.mark.asyncio


async def test_function_step_uses_canonical_self_scoped_use_case(tmp_path) -> None:
    pod_id = uuid4()
    user_id = uuid4()
    function_id = uuid4()
    root = tmp_path / "bundle"
    resource = root / "functions" / "triage"
    resource.mkdir(parents=True)
    (resource / "triage.json").write_text(
        json.dumps(
            {
                "name": "triage",
                "description": "${description}",
                "type": "JOB",
                "code": "def triage():\n    return None\n",
            }
        ),
        encoding="utf-8",
    )
    use_cases = SimpleNamespace(
        upsert_function_for_import=AsyncMock(
            return_value=SimpleNamespace(id=function_id)
        )
    )
    runner = FunctionStepRunner(
        uow_factory=object(),
        use_cases=use_cases,
    )

    await runner.run(
        PlanStep(
            index=0,
            kind=StepKind.FUNCTION,
            name="triage",
            action=StepAction.CREATE,
        ),
        pod_id=pod_id,
        user_id=user_id,
        bundle_root=root,
        replacements={"description": "Imported safely"},
    )

    call = use_cases.upsert_function_for_import.await_args.kwargs
    assert call["entity"].pod_id == pod_id
    assert call["entity"].description == "Imported safely"
    assert call["entity"].type == FunctionType.JOB
    assert call["update_entity"].code.startswith("def triage")
    assert call["user_id"] == user_id
