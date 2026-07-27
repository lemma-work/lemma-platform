from enum import Enum


class AgentHostCheckpoint(str, Enum):
    ACCEPTED = "ACCEPTED"
    DISPATCH_INTENT = "DISPATCH_INTENT"
    PROVIDER_ACCEPTED = "PROVIDER_ACCEPTED"
    RECOVERING = "RECOVERING"
    RUNNING = "RUNNING"
    TERMINAL = "TERMINAL"

    def __str__(self) -> str:
        return str(self.value)
