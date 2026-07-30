from enum import Enum


class AgentHostStatus(str, Enum):
    DRAINING = "DRAINING"
    OFFLINE = "OFFLINE"
    ONLINE = "ONLINE"
    REVOKED = "REVOKED"
    UPGRADE_REQUIRED = "UPGRADE_REQUIRED"

    def __str__(self) -> str:
        return str(self.value)
