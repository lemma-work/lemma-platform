from enum import Enum


class AgentHostRejectionCode(str, Enum):
    ADAPTER_UNAVAILABLE = "ADAPTER_UNAVAILABLE"
    CAPACITY_LOST = "CAPACITY_LOST"
    COMMAND_EXPIRED = "COMMAND_EXPIRED"
    CONFIG_REVISION_STALE = "CONFIG_REVISION_STALE"
    DRAINING = "DRAINING"
    HARNESS_NOT_FOUND = "HARNESS_NOT_FOUND"
    INVALID_COMMAND = "INVALID_COMMAND"

    def __str__(self) -> str:
        return str(self.value)
