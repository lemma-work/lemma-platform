from enum import Enum


class AgentRunStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    STOP_REQUESTED = "STOP_REQUESTED"

    def __str__(self) -> str:
        return str(self.value)
