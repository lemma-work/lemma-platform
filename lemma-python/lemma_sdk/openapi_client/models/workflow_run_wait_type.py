from enum import Enum


class WorkflowRunWaitType(str, Enum):
    AGENT = "AGENT"
    DECISION = "DECISION"
    FUNCTION = "FUNCTION"
    HUMAN = "HUMAN"
    QUEUED = "QUEUED"
    TIME = "TIME"

    def __str__(self) -> str:
        return str(self.value)
