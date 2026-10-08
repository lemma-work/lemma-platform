"""Asking a decision from inside the backend.

The same call `POST /pods/{pod_id}/decisions` makes, for a module that is
already running on someone's behalf -- a schedule filtering an event, a
workflow step choosing a branch. Errors are the contract's: retry on
`DecisionUnavailableError` and `DecisionLimitedError`, fix the request on
`DecisionInvalidError`, and treat usage's `UsageLimitExceededError` as spend
that has run out.
"""

from __future__ import annotations

from app.modules.decisions.contracts import (
    DecisionCaller,
    DecisionMaker,
    DecisionRequest,
    DecisionResult,
)
from app.modules.decisions.domain.questions import parse_schema


async def decide(request: DecisionRequest, *, caller: DecisionCaller) -> DecisionResult:
    return await decision_maker().decide(request, caller)


def decision_maker() -> DecisionMaker:
    from app.modules.decisions.services.decision_service import DecisionService

    return DecisionService()


def decision_schema_problems(raw: object) -> list[str]:
    """What is wrong with a schema of questions, for checking one when it is saved.

    Empty when `raw` is a valid set of questions. A workflow node or a schedule
    that stores questions to ask later checks them here, so the person saving
    them hears about a mistake then rather than on the first event.
    """
    _, problems = parse_schema(raw)
    return [f"{problem['path']}: {problem['message']}" for problem in problems]


__all__ = ["decide", "decision_maker", "decision_schema_problems"]
