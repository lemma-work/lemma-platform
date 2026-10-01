from enum import Enum


class ScheduleFireStatus(str, Enum):
    ERROR = "ERROR"
    FILTERED = "FILTERED"
    HELD = "HELD"
    TRIGGERED = "TRIGGERED"

    def __str__(self) -> str:
        return str(self.value)
