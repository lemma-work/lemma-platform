"""Every question a line can ask has something that takes its answer.

A question names its asker by type (``QuestionAsker``: ``TOOL``,
``WORKFLOW``, ``MESSAGE``), and answering looks the asker up by it
(``answers_for``). A type nobody registered is a question whose answer is
recorded and acted on by nothing -- the paused run never resumes, the
workflow never moves. So: every module that asks a question
(``NewQuestion``) is listed here with the asker it asks as, and each finds its
answerer once the worker's modules are loaded. A new asker fails the first
test until it is added below, and the second until it registers.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.modules.channels.domain.entities import QuestionAsker

APP = Path(__file__).resolve().parents[4]

#: Each module that asks, and what it asks as.
ASKERS: dict[str, QuestionAsker] = {
    "modules/agent/services/pause_questions.py": QuestionAsker.TOOL,
    "modules/agent/infrastructure/tools/messaging/pydantic_adapter.py": QuestionAsker.MESSAGE,
    "modules/workflow/infrastructure/form_questions.py": QuestionAsker.WORKFLOW,
}


def _asks_a_question(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "NewQuestion"
        for node in ast.walk(tree)
    )


def test_every_module_that_asks_a_question_is_listed() -> None:
    asking = {
        str(path.relative_to(APP))
        for path in APP.rglob("*.py")
        if "tests" not in path.parts and _asks_a_question(path)
    }
    assert asking == set(ASKERS)


def test_every_asker_has_a_registered_answerer() -> None:
    from app.modules.channels.services.questions import registered_askers
    from app.worker import register_worker_modules

    register_worker_modules()

    registered = registered_askers()
    for module, kind in ASKERS.items():
        assert kind in registered, f"{module} asks as {kind}: nothing answers it"
    assert set(ASKERS.values()) == set(QuestionAsker)
