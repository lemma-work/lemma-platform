from enum import Enum


class AgentHostCommandKind(str, Enum):
    CANCEL_RUN = "CANCEL_RUN"
    RESOLVE_PERMISSION = "RESOLVE_PERMISSION"
    START_RUN = "START_RUN"

    def __str__(self) -> str:
        return str(self.value)
