"""Architecture guard: saga/streaming controllers must not hand-roll the
connection-release orchestration.

The pattern is: controllers translate HTTP <-> a use-case call; the short-UoW
scoping + auth-context wiring lives in ``app/core/authorization/scope.py`` and the
per-module use-case layer. If any of these primitives leak back into a controller
module's namespace, a contributor has re-introduced the hand-rolled preamble this
refactor removed — fail loudly so it's caught in review, not under load.
"""

from __future__ import annotations

import importlib

import pytest

# Controllers fully migrated onto the use-case layer (their sagas live elsewhere).
_MIGRATED_CONTROLLERS = [
    "app.modules.datastore.api.controllers.file_controller",
    "app.modules.apps.api.controllers.app_controller",
    "app.modules.apps.api.controllers.public_app_controller",
    "app.modules.function.api.controllers.function_controller",
    "app.modules.agent.api.controllers.conversation_controller",
]

# Hand-rolled wiring that belongs in scope.py / the use-case layer, never a
# controller. (pod_context_scope is allowed — it IS the shared primitive — and the
# conversation controller uses it directly for its streaming endpoints.)
_FORBIDDEN_NAMES = [
    "resolve_pod_context",
    "set_current_context",
    "reset_current_context",
    "build_file_service",
    "build_app_service",
    "build_function_service",
    "build_function_service_with_factory",
]


@pytest.mark.parametrize("module_path", _MIGRATED_CONTROLLERS)
def test_controller_does_not_hand_roll_orchestration(module_path: str):
    module = importlib.import_module(module_path)
    leaked = [name for name in _FORBIDDEN_NAMES if hasattr(module, name)]
    assert not leaked, (
        f"{module_path} imports hand-rolled orchestration {leaked}; route it "
        "through the use-case layer / pod_context_scope instead."
    )
