from enum import Enum


class AutomationKind(str, Enum):
    SCHEDULE = "SCHEDULE"
    WORKFLOW = "WORKFLOW"

    def __str__(self) -> str:
        return str(self.value)
