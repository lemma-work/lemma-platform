from enum import Enum


class WorkflowEdgeKind(str, Enum):
    FAILURE = "FAILURE"
    NEXT = "NEXT"

    def __str__(self) -> str:
        return str(self.value)
