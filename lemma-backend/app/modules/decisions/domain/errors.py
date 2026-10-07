"""The three ways a decision does not come back as answers.

A caller has to tell them apart to do the right thing next, which is the whole
reason they are separate types: fix the request, wait and ask again, or retry
later. An unsure answer is none of these -- it is an answer, and comes back as
one.
"""

from __future__ import annotations

from typing import Literal

from app.core.domain.errors import DomainError

UnavailableReason = Literal[
    "timeout",
    "transport",
    "provider_error",
    "invalid_output",
    "token_limit",
    "not_configured",
]


class DecisionInvalidError(DomainError):
    """The request cannot be asked as sent. Retrying it unchanged cannot help."""

    def __init__(
        self,
        problems: list[dict[str, str]],
        *,
        message: str = "The decision request is not valid.",
        code: str = "DECISION_INVALID_REQUEST",
    ) -> None:
        super().__init__(message, code=code, status_code=422, details=problems)
        self.problems = problems


class DecisionLimitedError(DomainError):
    """Too many decisions for this organization this minute. Ask again later."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            "Too many decisions this minute. Try again shortly.",
            code="DECISION_RATE_LIMITED",
            status_code=429,
            details={"retry_after_seconds": retry_after_seconds},
        )
        self.retry_after_seconds = retry_after_seconds


class DecisionUnavailableError(DomainError):
    """The provider did not give a usable answer. The same request may succeed later.

    `invalid_output` is here rather than with the request errors because it is
    the provider's failure, not the caller's: the request was valid and the
    answer that came back was not.
    """

    def __init__(self, reason: UnavailableReason, message: str | None = None) -> None:
        super().__init__(
            message or _MESSAGES[reason],
            code="DECISION_PROVIDER_UNAVAILABLE",
            status_code=503,
            details={"reason": reason},
        )
        self.reason: UnavailableReason = reason


_MESSAGES: dict[UnavailableReason, str] = {
    "timeout": "The decision provider did not answer in time.",
    "transport": "The decision provider could not be reached.",
    "provider_error": "The decision provider failed to answer.",
    "invalid_output": "The decision provider's answer did not fit the questions.",
    "token_limit": (
        "The decision needed more model tokens than one decision may use. "
        "Send less evidence or fewer examples."
    ),
    "not_configured": "No decision provider is configured on this server.",
}
