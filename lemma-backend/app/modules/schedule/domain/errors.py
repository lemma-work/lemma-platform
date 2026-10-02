"""Domain errors for schedule module."""

from typing import ClassVar
from uuid import UUID

from app.core.domain.errors import DomainError


class ScheduleDomainError(DomainError):
    def __init__(
        self,
        message: str,
        code: str = "SCHEDULE_ERROR",
        status_code: int = 400,
    ):
        super().__init__(message=message, code=code, status_code=status_code)


class ScheduleValidationError(ScheduleDomainError):
    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="SCHEDULE_VALIDATION_ERROR",
            status_code=422,
        )


class ScheduleSourceEventIdRequiredError(ScheduleValidationError):
    def __init__(self):
        super().__init__(
            "A stable provider event identifier is required for schedule delivery"
        )
        self.code = "SCHEDULE_SOURCE_EVENT_ID_REQUIRED"


class ScheduleTooFrequentError(ScheduleValidationError):
    def __init__(self, minimum_interval_minutes: int):
        unit = "minute" if minimum_interval_minutes == 1 else "minutes"
        super().__init__(
            "Time schedules cannot run more frequently than every "
            f"{minimum_interval_minutes} {unit}."
        )
        self.code = "SCHEDULE_TOO_FREQUENT"


class ScheduleNotFoundError(ScheduleDomainError):
    def __init__(self, message: str = "Schedule not found"):
        super().__init__(message=message, code="SCHEDULE_NOT_FOUND", status_code=404)


class ScheduleRunNotRetryableError(ScheduleDomainError):
    def __init__(self):
        super().__init__(
            message="Schedule run is not failed, dead-lettered, or does not exist",
            code="SCHEDULE_RUN_NOT_RETRYABLE",
            status_code=409,
        )


class ScheduleAccessDeniedError(ScheduleDomainError):
    def __init__(self, message: str = "Access denied"):
        super().__init__(
            message=message,
            code="SCHEDULE_ACCESS_DENIED",
            status_code=403,
        )


class ScheduleInfrastructureError(ScheduleDomainError):
    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="SCHEDULE_INFRASTRUCTURE_ERROR",
            status_code=503,
        )


class ScheduleFilterUndecidedError(DomainError):
    """A schedule's filter could not say whether an event should fire it.

    Every rung that could be asked was, and none committed: System One passed,
    or was not configured, and the model said it could not tell. That is a
    failed evaluation, not a skip, and it is final -- the decision is recorded
    under the event and asking again returns the same open one -- so the fire
    ends as failed and counts toward the schedule's breaker. A decision left
    open by a provider failure is `ScheduleFilterInterruptedError` instead.

    Never reaches a client: the worker that ran the filter records it.
    """

    decision_id: UUID | None

    #: The failed run's `error_type`, and what the schedule's `last_error` says.
    error_type: ClassVar[str] = "ScheduleFilterUndecided"
    explanation: ClassVar[str] = (
        "The filter could not decide whether this event should fire the schedule"
    )

    def __init__(self, decision_id: UUID | None) -> None:
        # 422, not 503: an inbox-backed consumer retries a 503 and finishes
        # anything else, and an undecided evaluation is final.
        super().__init__(
            self.explanation,
            code="SCHEDULE_FILTER_UNDECIDED",
            status_code=422,
        )
        self.decision_id = decision_id


class ScheduleTriageUndecidedError(ScheduleFilterUndecidedError):
    """A schedule's triage could not say what to do with an event.

    The question was left open with no fallback the routes cover, or the
    decider answered an option the schedule does not route. A failed
    evaluation, recorded and counted exactly as an undecided filter is.
    """

    error_type: ClassVar[str] = "ScheduleTriageUndecided"
    explanation: ClassVar[str] = (
        "The triage could not decide what to do with this event"
    )


class ScheduleTriageDeciderMissingError(ScheduleFilterUndecidedError):
    """The decider a schedule's triage asks has been deleted.

    Nothing was asked, so there is no decision to point at. Counted on the
    breaker like any fire that cannot succeed until a person repoints it.
    """

    error_type: ClassVar[str] = "ScheduleTriageDeciderMissing"
    explanation: ClassVar[str] = (
        "The decider this schedule's triage asks no longer exists"
    )

    def __init__(self) -> None:
        super().__init__(None)


class ScheduleFilterInterruptedError(DomainError):
    """The filter's decision is open because a rung failed, not because it was unsure.

    Transient: a spent System One budget, a provider error. Deliberately not
    caught by the filter's callers, so the event goes back through their retry
    -- and the decisions module asks an interrupted decision again rather than
    returning the recorded open one.
    """

    decision_id: UUID

    def __init__(self, decision_id: UUID) -> None:
        # 503 is what an inbox-backed consumer retries; anything else it
        # finishes, which would turn a provider blip into a lost event.
        super().__init__(
            "the schedule filter was interrupted before it could decide",
            code="SCHEDULE_FILTER_INTERRUPTED",
            status_code=503,
        )
        self.decision_id = decision_id


class ScheduleTriageInterruptedError(ScheduleFilterInterruptedError):
    """A triage's decision is open because a rung failed; retried like a filter's."""
