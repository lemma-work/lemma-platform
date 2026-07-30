from enum import Enum


class AgentHostCommandKind(str, Enum):
    CANCEL_RUN = "CANCEL_RUN"
    START_RUN = "START_RUN"

    def __str__(self) -> str:
        return str(self.value)
