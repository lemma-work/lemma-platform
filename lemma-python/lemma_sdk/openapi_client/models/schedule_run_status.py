from enum import Enum


class ScheduleRunStatus(str, Enum):
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    DEAD_LETTERED = "DEAD_LETTERED"
    DISPATCHED = "DISPATCHED"
    FAILED = "FAILED"
    FILTERED = "FILTERED"
    PROCESSING = "PROCESSING"
    RECEIVED = "RECEIVED"
    TARGET_FAILED = "TARGET_FAILED"

    def __str__(self) -> str:
        return str(self.value)
