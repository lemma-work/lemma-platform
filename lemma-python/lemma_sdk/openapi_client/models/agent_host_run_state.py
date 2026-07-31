from enum import Enum


class AgentHostRunState(str, Enum):
    ACCEPTED = "ACCEPTED"
    CANCELLED = "CANCELLED"
    DISPATCHING = "DISPATCHING"
    DISPATCH_UNKNOWN = "DISPATCH_UNKNOWN"
    FAILED = "FAILED"
    LEASED = "LEASED"
    QUEUED_FOR_HOST = "QUEUED_FOR_HOST"
    RECOVERING = "RECOVERING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    WAITING_INPUT = "WAITING_INPUT"

    def __str__(self) -> str:
        return str(self.value)
