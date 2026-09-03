"""Who each caller of a connector operation presents as.

The presenter tests cover the decision. These cover the *inputs* to it, which is
where the bug actually was: every caller reaches the same resolver, and one of
them asking to be the app made all of them the app.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.modules.connectors.domain.execution_plan import ResolvedConnectorExecution
from app.modules.connectors.domain.kinds import ExecutionRequest
from app.modules.connectors.services.connector_operation_service import (
    ConnectorOperationService,
)

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[5]


def test_every_layer_defaults_to_the_person():
    """Omission has to mean "the person" at every hop, not just the last one.

    A default of "app" anywhere in the chain would put pod publish back on a bot
    identity, and the only visible symptom would be the commit author on
    somebody's published repository.
    """
    assert ExecutionRequest.__dataclass_fields__["act_as"].default == "user"
    assert ResolvedConnectorExecution.__dataclass_fields__["act_as"].default == "user"
    for name in ("resolve_execution", "resolve_execution_for_auth_config"):
        signature = inspect.signature(getattr(ConnectorOperationService, name))
        assert signature.parameters["act_as"].default == "user", name


def _callers_asking_to_be_the_app() -> set[str]:
    """Every source line that passes `act_as="app"`, by file."""
    found: set[str] = set()
    for path in (BACKEND / "app").rglob("*.py"):
        if "tests" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - not our code to fix
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "act_as":
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant) and value.value == "app":
                    found.add(path.relative_to(BACKEND).as_posix())
    return found


def test_only_the_agent_tool_path_asks_to_be_the_app():
    """A deliberately brittle list.

    Adding a caller here is a decision about whose name ends up on somebody's
    commits, and it should be made on purpose rather than by copying a nearby
    call. Pod publish and pod import must never appear.
    """
    assert _callers_asking_to_be_the_app() == {
        "app/modules/agent/tools/connectors/pydantic_adapter.py"
    }


def test_pod_bundle_never_asks_to_be_the_app():
    """Publish writes the commit; import reads a repository that may be one the
    App was never installed on. Both are the person's."""
    for path in (BACKEND / "app" / "modules" / "pod_bundle").rglob("*.py"):
        assert "act_as" not in path.read_text(encoding="utf-8"), path
