"""Domain errors for schedule module."""

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
