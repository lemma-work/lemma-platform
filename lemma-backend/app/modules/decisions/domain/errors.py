"""Errors the decisions module raises, and the codes clients match on."""

from __future__ import annotations

from enum import StrEnum

from app.core.domain.errors import DomainError


class DecisionErrorCode(StrEnum):
    DECIDER_NOT_FOUND = "DECIDER_NOT_FOUND"
    DECIDER_EXISTS = "DECIDER_EXISTS"
    DECIDER_INVALID = "DECIDER_INVALID"
    DECISION_NOT_FOUND = "DECISION_NOT_FOUND"
    DECISION_REQUEST_INVALID = "DECISION_REQUEST_INVALID"
    DECISION_ANSWER_INVALID = "DECISION_ANSWER_INVALID"
    DECISION_ROWS_TOO_MANY = "DECISION_ROWS_TOO_MANY"
    DECISION_ROWS_UNREADABLE = "DECISION_ROWS_UNREADABLE"
    DECISION_NEEDS_PERSON = "DECISION_NEEDS_PERSON"


class DeciderNotFoundError(DomainError):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"No decider named {name!r} in this pod.",
            code=DecisionErrorCode.DECIDER_NOT_FOUND,
            status_code=404,
        )


class DeciderExistsError(DomainError):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"A decider named {name!r} already exists in this pod.",
            code=DecisionErrorCode.DECIDER_EXISTS,
            status_code=409,
        )


class DeciderInvalidError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__(
            message, code=DecisionErrorCode.DECIDER_INVALID, status_code=422
        )


class DecisionNotFoundError(DomainError):
    def __init__(self) -> None:
        super().__init__(
            "No such decision, or it is not yours to see.",
            code=DecisionErrorCode.DECISION_NOT_FOUND,
            status_code=404,
        )


class DecisionRequestInvalidError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__(
            message, code=DecisionErrorCode.DECISION_REQUEST_INVALID, status_code=422
        )


class DecisionAnswerInvalidError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__(
            message, code=DecisionErrorCode.DECISION_ANSWER_INVALID, status_code=422
        )


class DecisionRowsTooManyError(DomainError):
    def __init__(self, limit: int) -> None:
        super().__init__(
            f"At most {limit} rows can be decided in one request. Split the rows "
            "into smaller batches.",
            code=DecisionErrorCode.DECISION_ROWS_TOO_MANY,
            status_code=422,
        )


class DecisionRowsUnreadableError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__(
            message, code=DecisionErrorCode.DECISION_ROWS_UNREADABLE, status_code=422
        )


class DecisionNeedsPersonError(DomainError):
    """A request with no person behind it, where a decision is read for one."""

    def __init__(self) -> None:
        super().__init__(
            "Decisions are read on a person's behalf.",
            code=DecisionErrorCode.DECISION_NEEDS_PERSON,
            status_code=403,
        )
