from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.function.domain.entities import FunctionType
from app.modules.pod_bundle.domain.errors import PartKeptError
from app.modules.pod_bundle.domain.state import PlanStep, StepAction, StepKind
from app.modules.pod_bundle.infrastructure.function_builder import FunctionStepRunner
from app.modules.pod_bundle.infrastructure.kept_parts import CreatedParts


pytestmark = pytest.mark.asyncio


def _bundle(tmp_path) -> Path:
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
    return root


def _step() -> PlanStep:
    return PlanStep(
        index=0, kind=StepKind.FUNCTION, name="triage", action=StepAction.CREATE
    )


async def test_function_step_uses_canonical_self_scoped_use_case(tmp_path) -> None:
    pod_id = uuid4()
    user_id = uuid4()
    use_cases = SimpleNamespace(
        create_function_for_import=AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    )
    created = CreatedParts()

    await FunctionStepRunner(uow_factory=object(), use_cases=use_cases).run(
        _step(),
        pod_id=pod_id,
        user_id=user_id,
        bundle_root=_bundle(tmp_path),
        replacements={"description": "Imported safely"},
        created=created,
    )

    call = use_cases.create_function_for_import.await_args.kwargs
    assert call["entity"].pod_id == pod_id
    assert call["entity"].description == "Imported safely"
    assert call["entity"].type == FunctionType.JOB
    assert call["code"].startswith("def triage")
    assert call["user_id"] == user_id
    assert created.made(StepKind.FUNCTION, "triage")


async def test_a_function_someone_else_made_is_kept_and_said_so(tmp_path) -> None:
    """The function module found the name taken, in the unit of work that
    would have created it, and wrote nothing: its code, schema and access stay
    its maker's, and the step is skipped with the reason."""
    use_cases = SimpleNamespace(create_function_for_import=AsyncMock(return_value=None))
    created = CreatedParts()

    with pytest.raises(PartKeptError, match="kept as it is"):
        await FunctionStepRunner(uow_factory=object(), use_cases=use_cases).run(
            _step(),
            pod_id=uuid4(),
            user_id=uuid4(),
            bundle_root=_bundle(tmp_path),
            replacements={},
            created=created,
        )

    assert not created.made(StepKind.FUNCTION, "triage")


async def test_a_function_this_import_made_before_a_crash_is_its_own(tmp_path) -> None:
    use_cases = SimpleNamespace(create_function_for_import=AsyncMock(return_value=None))
    created = CreatedParts()

    await FunctionStepRunner(uow_factory=object(), use_cases=use_cases).run(
        _step(),
        pod_id=uuid4(),
        user_id=uuid4(),
        bundle_root=_bundle(tmp_path),
        replacements={},
        created=created,
        resumed=True,
    )

    assert created.made(StepKind.FUNCTION, "triage")
