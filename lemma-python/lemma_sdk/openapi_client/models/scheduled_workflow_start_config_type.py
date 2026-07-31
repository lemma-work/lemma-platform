from enum import Enum


class ScheduledWorkflowStartConfigType(str, Enum):
    CRON = "CRON"
    ONCE = "ONCE"

    def __str__(self) -> str:
        return str(self.value)
