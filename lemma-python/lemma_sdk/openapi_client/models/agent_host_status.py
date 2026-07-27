from enum import Enum


class AgentHostStatus(str, Enum):
    DEGRADED = "DEGRADED"
    DRAINING = "DRAINING"
    OFFLINE = "OFFLINE"
    ONLINE = "ONLINE"
    REVOKED = "REVOKED"
    UPGRADE_REQUIRED = "UPGRADE_REQUIRED"

    def __str__(self) -> str:
        return str(self.value)
