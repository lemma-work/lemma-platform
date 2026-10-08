"""What other modules use to ask a decision: the types, the errors, the port.

A leaf: this module's own domain, the standard library and pydantic, nothing
that resolves a model or opens a connection. Asking lives in `decide.py`, so a
module that only names the types does not pay for the provider stack.
"""

from typing import Protocol

from app.modules.decisions.domain.answers import (
    Answer,
    AnswerValue,
    DecisionResult,
    DecisionUsage,
)
from app.modules.decisions.domain.errors import (
    DecisionInvalidError,
    DecisionLimitedError,
    DecisionUnavailableError,
)
from app.modules.decisions.domain.request import (
    DecisionCaller,
    DecisionExample,
    DecisionPriority,
    DecisionRequest,
)


class DecisionMaker(Protocol):
    """Asks decisions. Inject one to keep a caller testable without a provider."""

    async def decide(
        self, request: DecisionRequest, caller: DecisionCaller
    ) -> DecisionResult: ...


__all__ = [
    "Answer",
    "AnswerValue",
    "DecisionCaller",
    "DecisionExample",
    "DecisionInvalidError",
    "DecisionLimitedError",
    "DecisionMaker",
    "DecisionPriority",
    "DecisionRequest",
    "DecisionResult",
    "DecisionUnavailableError",
    "DecisionUsage",
]
