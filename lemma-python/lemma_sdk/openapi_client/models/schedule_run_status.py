from enum import Enum


class ScheduleRunStatus(str, Enum):
    DEAD_LETTERED = "DEAD_LETTERED"
    DISPATCHED = "DISPATCHED"
    FAILED = "FAILED"
    FILTERED = "FILTERED"
    PROCESSING = "PROCESSING"
    RECEIVED = "RECEIVED"

    def __str__(self) -> str:
        return str(self.value)
